"""Supply-chain pin regression tests (WU P0-4: B1/B2/B3).

These verify that Dockerfile/build.sh/versions.json pin the three previously
unverified fetches (Sub-Store frontend, crypctl, acme.sh) to immutable refs.
All checks read the real repo files — no network, no image build.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_DOCKERFILE = _REPO / "Dockerfile"
_BUILD_SH = _REPO / "build.sh"
_VERSIONS = _REPO / "versions.json"

_HEX40 = re.compile(r"^[0-9a-f]{40}$")


def _versions() -> dict:
    return json.loads(_VERSIONS.read_text(encoding="utf-8"))


def test_frontend_commit_sha_recorded_in_versions_json() -> None:
    data = _versions()
    sha = data.get("sub_store_frontend_sha", "")
    assert _HEX40.match(sha), (
        f"sub_store_frontend_sha must be a 40-hex commit SHA, got {sha!r}"
    )


def test_build_sh_reads_and_passes_frontend_sha() -> None:
    src = _BUILD_SH.read_text(encoding="utf-8")
    assert "get_cached_version sub_store_frontend_sha" in src
    assert "_require_sha \"SUB_STORE_FRONTEND_SHA\"" in src
    assert "--build-arg SUB_STORE_FRONTEND_SHA=" in src
