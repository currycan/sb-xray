"""§4: committed compose/Dockerfile must not carry env-specific internal info."""
from __future__ import annotations

from pathlib import Path

_COMPOSE = Path("docker-compose.yml").read_text(encoding="utf-8")
_DOCKERFILE = Path("Dockerfile").read_text(encoding="utf-8")


def test_reverse_domains_default_empty_compose() -> None:
    # E2: no internal .lan hostnames baked as the committed default
    assert ".lan" not in _COMPOSE
    assert 'REVERSE_DOMAINS=${REVERSE_DOMAINS:-}' in _COMPOSE


def test_reverse_domains_default_empty_dockerfile() -> None:
    assert ".lan" not in _DOCKERFILE
    assert 'ENV REVERSE_DOMAINS=""' in _DOCKERFILE
