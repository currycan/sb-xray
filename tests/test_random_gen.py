"""Tests for sb_xray.random_gen (entrypoint.sh §4 equivalent)."""

from __future__ import annotations

import re
import uuid as _uuid

import pytest
from sb_xray import random_gen as rg


def test_port_in_valid_range() -> None:
    for _ in range(100):
        port = rg.generate("port")
        value = int(port)
        assert 32000 <= value <= 38000


def test_port_kind_returns_string() -> None:
    assert isinstance(rg.generate("port"), str)


def test_uuid_is_valid_uuid4() -> None:
    value = rg.generate("uuid")
    parsed = _uuid.UUID(value)
    assert parsed.version == 4
    assert str(parsed) == value


def test_password_default_length() -> None:
    pw = rg.generate("password")
    assert len(pw) == 12
    assert re.match(r"^[A-Za-z0-9]+$", pw)


@pytest.mark.parametrize("length", [1, 8, 32, 64, 128])
def test_password_custom_length(length: int) -> None:
    pw = rg.generate("password", length=length)
    assert len(pw) == length
    assert re.match(r"^[A-Za-z0-9]+$", pw)


def test_path_default_length() -> None:
    p = rg.generate("path")
    assert len(p) == 12
    assert re.match(r"^[a-z0-9]+$", p)


def test_path_only_lowercase_alnum() -> None:
    for _ in range(50):
        p = rg.generate("path", length=20)
        assert re.match(r"^[a-z0-9]{20}$", p)


def test_hex_generates_hex_string() -> None:
    h = rg.generate("hex", length=16)
    assert len(h) == 32  # token_hex(16) -> 32 chars
    assert re.match(r"^[0-9a-f]+$", h)


def test_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        rg.generate("nonsense")  # type: ignore[arg-type]


def test_passwords_are_unique() -> None:
    samples = {rg.generate("password", length=32) for _ in range(100)}
    assert len(samples) == 100


def test_uuid_samples_are_unique() -> None:
    samples = {rg.generate("uuid") for _ in range(50)}
    assert len(samples) == 50
