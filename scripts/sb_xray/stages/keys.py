"""Key-pair generators backed by the ``xray`` CLI.

Re-uses ``EnvManager.ensure_key_pair`` so the two halves of each pair land
in ``ENV_FILE`` atomically (entrypoint.sh §6 ``ensure_key_pair`` parity).
"""

from __future__ import annotations

import subprocess

from sb_xray import logging as sblog
from sb_xray.env import EnvManager


def _run_xray(cmd: list[str]) -> list[str]:
    """Run ``cmd`` and return stdout lines, raising on non-zero exit."""
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return [ln for ln in result.stdout.splitlines() if ln.strip()]


def _parse_two_line_pair(lines: list[str]) -> tuple[str, str]:
    """``xray x25519`` / ``xray mlkem768`` emit ``Label: value`` pairs.

    Bash uses ``awk -F': ' '{print $2}'`` on lines 1 + 2; we match that.
    """
    if len(lines) < 2:
        raise RuntimeError(f"xray output too short: {lines!r}")

    def _value(line: str) -> str:
        if ": " in line:
            return line.split(": ", 1)[1].strip()
        return line.strip()

    return _value(lines[0]), _value(lines[1])


def ensure_reality_keys(mgr: EnvManager) -> dict[str, str]:
    """Fill ``XRAY_REALITY_PRIVATE_KEY`` / ``XRAY_REALITY_PUBLIC_KEY``."""

    def _gen() -> dict[str, str]:
        priv, pub = _parse_two_line_pair(_run_xray(["xray", "x25519"]))
        return {
            "XRAY_REALITY_PRIVATE_KEY": priv,
            "XRAY_REALITY_PUBLIC_KEY": pub,
        }

    return mgr.ensure_key_pair(
        "Reality",
        "XRAY_REALITY_PRIVATE_KEY",
        "XRAY_REALITY_PUBLIC_KEY",
        generator=_gen,
    )


def ensure_mlkem_keys(mgr: EnvManager) -> dict[str, str]:
    """Fill ``XRAY_MLKEM768_SEED`` / ``XRAY_MLKEM768_CLIENT``."""

    def _gen() -> dict[str, str]:
        seed, client = _parse_two_line_pair(_run_xray(["xray", "mlkem768"]))
        return {
            "XRAY_MLKEM768_SEED": seed,
            "XRAY_MLKEM768_CLIENT": client,
        }

    return mgr.ensure_key_pair(
        "MLKEM768",
        "XRAY_MLKEM768_SEED",
        "XRAY_MLKEM768_CLIENT",
        generator=_gen,
    )


def ensure_all_keys(mgr: EnvManager) -> None:
    """Ensure every key pair the downstream templates rely on."""
    sblog.log("INFO", "[阶段 3] 生成加密密钥对...")
    ensure_reality_keys(mgr)
    ensure_mlkem_keys(mgr)
