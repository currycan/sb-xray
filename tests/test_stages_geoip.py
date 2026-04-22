"""Tests for scripts/sb_xray/stages/geoip.py (thin wrapper over sb_xray.geo)."""

from __future__ import annotations

import pytest
from sb_xray import geo
from sb_xray.stages import geoip as sbgeo


def test_delegates_to_geo_refresh_on_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_refresh(*, on_startup: bool) -> int:
        captured["on_startup"] = on_startup
        return 0

    monkeypatch.setattr(geo, "refresh", fake_refresh)
    assert sbgeo.update_geo_data() == 0
    assert captured["on_startup"] is True


def test_surfaces_failure_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geo, "refresh", lambda *, on_startup: 2)
    assert sbgeo.update_geo_data() == 2
