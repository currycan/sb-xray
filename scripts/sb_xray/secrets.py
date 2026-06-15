"""Remote secrets decryption + change-aware refresh.

Downloads an encrypted blob from the public key repo and decrypts it
locally using ``crypctl`` with ``--key-env DECODE`` (keeps the passphrase
out of ``argv`` / ``ps``).

Two entry points:

- :func:`decrypt_remote_secrets` - boot cold-start parity: ensure
  ``secret_file`` exists, fetching + decrypting only when it is missing.
- :func:`refresh_remote_secrets` - change-aware refresh: always re-fetch +
  decrypt to a sidecar temp file and compare against the on-disk plaintext,
  atomically replacing it only when the decrypted credentials actually
  changed. Drives both the boot freshness check and the ``secrets-refresh``
  cron, so a rotated ``tmp.bin`` reaches a long-running container without a
  manual ``.envs/secret`` wipe + container recreate.
"""

from __future__ import annotations

import enum
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx

logger = logging.getLogger(__name__)

_SECRETS_URL: Final[str] = "https://raw.githubusercontent.com/currycan/key/master/tmp.bin"
_DEFAULT_TMP: Final[Path] = Path("/tmp/tmp.bin")
_DOWNLOAD_TIMEOUT: Final[float] = 30.0

# Shell/bash bookkeeping vars that ``env`` reports but a credential file never
# "defines". Filtered out of :func:`parse_env_file` so the refresh diff keys on
# real credentials only.
_BASH_INTERNAL_VARS: Final[frozenset[str]] = frozenset(
    {
        "_",
        "PWD",
        "OLDPWD",
        "SHLVL",
        "IFS",
        "PS1",
        "PS2",
        "PS4",
        "UID",
        "EUID",
        "PPID",
        "RANDOM",
        "SECONDS",
        "LINENO",
        "HOSTNAME",
        "HOSTTYPE",
        "MACHTYPE",
        "OSTYPE",
        "SHELL",
    }
)


class SecretStatus(enum.Enum):
    SKIPPED = "skipped"
    DECRYPTED = "decrypted"


class RefreshStatus(enum.Enum):
    """Outcome of :func:`refresh_remote_secrets`."""

    COLD_DECRYPTED = "cold_decrypted"  # file was missing -> freshly fetched
    UPDATED = "updated"  # upstream plaintext differs -> file replaced
    UNCHANGED = "unchanged"  # upstream identical to on-disk
    SKIPPED_OFFLINE = "skipped_offline"  # fetch failed, cached file kept
    SKIPPED_NO_DECODE = "skipped_no_decode"  # DECODE unset, cached file kept


@dataclass(frozen=True)
class SecretRefresh:
    status: RefreshStatus
    changed_keys: frozenset[str] = frozenset()
    removed_keys: frozenset[str] = frozenset()

    @property
    def content_changed(self) -> bool:
        """True when the on-disk secret was (re)written with new content."""
        return self.status in (RefreshStatus.COLD_DECRYPTED, RefreshStatus.UPDATED)


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a shell credential file into a ``{key: value}`` dict.

    Delegates parsing to bash so quoted / ``export``-prefixed / inline-comment
    assignments are handled exactly like ``source`` would. Runs in a *clean*
    environment (``env -i``) so the result contains only what the file itself
    defines (minus bash bookkeeping vars) - not the caller's inherited env.
    That is what makes the refresh diff key on credentials alone. Missing /
    unreadable file -> ``{}``.
    """
    if not path.is_file():
        return {}

    try:
        result = subprocess.run(
            [
                "/usr/bin/env",
                "-i",
                "bash",
                "-c",
                'set -a; [ -f "$1" ] && . "$1"; env -0',
                "_",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        logger.warning("env-parse: bash source %s failed: %s", path, exc)
        return {}

    parsed: dict[str, str] = {}
    for record in result.stdout.split(b"\x00"):
        if not record:
            continue
        key_b, sep, value_b = record.partition(b"=")
        if not sep:
            continue
        try:
            key = key_b.decode("utf-8")
            value = value_b.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if key in _BASH_INTERNAL_VARS or key.startswith("BASH_"):
            continue
        parsed[key] = value
    return parsed


def _fetch_and_decrypt(out_path: Path, *, blob_path: Path) -> None:
    """Download the encrypted blob and decrypt it to ``out_path``.

    Raises ``RuntimeError`` on download error or non-zero ``crypctl`` exit;
    on failure ``out_path`` is left absent (never a half-written file).
    """
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(timeout=_DOWNLOAD_TIMEOUT) as client:
            resp = client.get(_SECRETS_URL)
            resp.raise_for_status()
            blob_path.write_bytes(resp.content)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"download failed: {exc}") from exc

    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "crypctl",
            "decrypt",
            "-i",
            str(blob_path),
            "-o",
            str(out_path),
            "--key-env",
            "DECODE",
        ],
        check=False,
    )
    blob_path.unlink(missing_ok=True)
    if result.returncode != 0:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(f"decrypt failed: crypctl exited with code {result.returncode}")


def decrypt_remote_secrets(
    *,
    secret_file: Path,
    tmp_path: Path | None = None,
) -> SecretStatus:
    """Ensure ``secret_file`` exists, fetching + decrypting when missing.

    Boot cold-start parity - an existing file is reused as-is (offline
    resilient). Use :func:`refresh_remote_secrets` to pick up an updated
    ``tmp.bin`` on an already-provisioned file. Raises ``RuntimeError`` on
    any failure (missing ``DECODE`` env, download error, decrypt non-zero
    exit).
    """
    if secret_file.is_file():
        return SecretStatus.SKIPPED

    if not os.environ.get("DECODE"):
        raise RuntimeError("DECODE environment variable is required to decrypt secrets")

    blob_path = tmp_path if tmp_path is not None else _DEFAULT_TMP
    _fetch_and_decrypt(secret_file, blob_path=blob_path)
    return SecretStatus.DECRYPTED


def refresh_remote_secrets(
    *,
    secret_file: Path,
    tmp_path: Path | None = None,
) -> SecretRefresh:
    """Re-fetch + decrypt ``tmp.bin`` and replace ``secret_file`` if it changed.

    Always reaches upstream (unless ``DECODE`` is unset). Decrypts to a sidecar
    temp file, compares the parsed plaintext against the current file, and only
    replaces the file when the credentials differ - so the common case is a
    cheap no-op and a real rotation flips ``content_changed`` to True with the
    exact ``changed``/``removed`` key sets for the caller to apply to env.

    Offline / no ``DECODE`` with a usable cached file on disk degrades to a
    ``SKIPPED_*`` status (never raises, never touches the running config). Only
    a *cold* start with no cached file and an unreachable upstream raises.
    """
    if not os.environ.get("DECODE"):
        if secret_file.is_file():
            logger.warning("DECODE unset - keeping cached secret at %s", secret_file)
            return SecretRefresh(RefreshStatus.SKIPPED_NO_DECODE)
        raise RuntimeError("DECODE environment variable is required to decrypt secrets")

    blob_path = tmp_path if tmp_path is not None else _DEFAULT_TMP
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    candidate = secret_file.with_name(secret_file.name + ".new")

    try:
        _fetch_and_decrypt(candidate, blob_path=blob_path)
    except RuntimeError as exc:
        candidate.unlink(missing_ok=True)
        if secret_file.is_file():
            logger.warning("secret refresh fetch failed (%s) - keeping cached %s", exc, secret_file)
            return SecretRefresh(RefreshStatus.SKIPPED_OFFLINE)
        raise

    if not secret_file.is_file():
        os.replace(candidate, secret_file)
        logger.info("secret cold-decrypted to %s", secret_file)
        return SecretRefresh(RefreshStatus.COLD_DECRYPTED)

    old_vars = parse_env_file(secret_file)
    new_vars = parse_env_file(candidate)
    if old_vars == new_vars:
        candidate.unlink(missing_ok=True)
        logger.info("secret refresh: upstream unchanged")
        return SecretRefresh(RefreshStatus.UNCHANGED)

    changed = frozenset(k for k, v in new_vars.items() if old_vars.get(k) != v)
    removed = frozenset(old_vars.keys() - new_vars.keys())
    os.replace(candidate, secret_file)
    logger.info(
        "secret refresh: updated (%d changed, %d removed keys)", len(changed), len(removed)
    )
    return SecretRefresh(RefreshStatus.UPDATED, changed, removed)
