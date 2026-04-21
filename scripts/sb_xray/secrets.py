"""Remote secrets decryption (entrypoint.sh §14 equivalent).

Downloads an encrypted blob from the public key repo and decrypts it
locally using ``crypctl`` with ``--key-env DECODE`` (keeps the passphrase
out of ``argv`` / ``ps``).
"""

from __future__ import annotations

import enum
import os
import subprocess
from pathlib import Path
from typing import Final

import httpx

_SECRETS_URL: Final[str] = "https://raw.githubusercontent.com/currycan/key/master/tmp.bin"
_DEFAULT_TMP: Final[Path] = Path("/tmp/tmp.bin")
_DOWNLOAD_TIMEOUT: Final[float] = 30.0


class SecretStatus(enum.Enum):
    SKIPPED = "skipped"
    DECRYPTED = "decrypted"


def decrypt_remote_secrets(
    *,
    secret_file: Path,
    tmp_path: Path | None = None,
) -> SecretStatus:
    """Ensure ``secret_file`` exists, fetching + decrypting when missing.

    Raises ``RuntimeError`` on any failure (missing ``DECODE`` env,
    download error, decrypt non-zero exit).
    """
    if secret_file.is_file():
        return SecretStatus.SKIPPED

    if not os.environ.get("DECODE"):
        raise RuntimeError("DECODE environment variable is required to decrypt secrets")

    blob_path = tmp_path if tmp_path is not None else _DEFAULT_TMP
    blob_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with httpx.Client(timeout=_DOWNLOAD_TIMEOUT) as client:
            resp = client.get(_SECRETS_URL)
            resp.raise_for_status()
            blob_path.write_bytes(resp.content)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"download failed: {exc}") from exc

    secret_file.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "crypctl",
            "decrypt",
            "-i",
            str(blob_path),
            "-o",
            str(secret_file),
            "--key-env",
            "DECODE",
        ],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"decrypt failed: crypctl exited with code {result.returncode}")

    blob_path.unlink(missing_ok=True)
    return SecretStatus.DECRYPTED
