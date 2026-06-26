"""Supply-chain build invariants: base images digest-pinned, GOPROXY aligned (B4/B5)."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE = REPO_ROOT / "docker-compose.yml"

_PIN_RE = re.compile(r"@sha256:[0-9a-f]{64}\b")


def _pinned(ref: str) -> bool:
    """A ref is pinned if it carries an @sha256:<64-hex> digest."""
    return bool(_PIN_RE.search(ref))


def _dockerfile_from_refs(text: str) -> list[str]:
    """Return the image ref of every `FROM <ref>` (ignoring `AS <stage>`)."""
    refs: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^\s*FROM\s+(\S+)", line)
        if m:
            refs.append(m.group(1))
    return refs


def test_every_dockerfile_from_is_digest_pinned() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    refs = _dockerfile_from_refs(text)
    assert refs, "no FROM lines parsed — Dockerfile path/parse broken"
    unpinned = [r for r in refs if not _pinned(r)]
    assert not unpinned, f"unpinned base images: {unpinned}"


def test_watchtower_image_is_digest_pinned() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    images = re.findall(r"^\s*image:\s*(\S+)", text, flags=re.MULTILINE)
    # Our own :latest image must stay floating for watchtower auto-update (§2).
    third_party = [i for i in images if "currycan/sb-xray" not in i]
    assert third_party, "no third-party image lines parsed"
    unpinned = [i for i in third_party if not _pinned(i)]
    assert not unpinned, f"unpinned third-party images: {unpinned}"
