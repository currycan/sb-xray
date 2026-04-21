"""Smoke tests: sb_xray package is importable and exposes __version__."""

from __future__ import annotations

import re


def test_import_sb_xray() -> None:
    import sb_xray

    assert sb_xray is not None


def test_version_matches_semver() -> None:
    import sb_xray

    assert hasattr(sb_xray, "__version__")
    assert re.match(r"^\d+\.\d+\.\d+$", sb_xray.__version__)


def test_routing_subpackage_importable() -> None:
    import sb_xray.routing

    assert sb_xray.routing is not None
