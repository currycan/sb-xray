"""Structured event emission (Phase 2).

One call site, two sinks:

1. **stdout** — always; a single INFO line ``event=<name> payload=<json>``.
   Operators reading ``docker logs`` get a history; log shippers (Loki,
   Grafana) can grep the ``event=`` prefix.
2. **shoutrrr** — optional; POSTs to the local forwarder at
   ``http://127.0.0.1:${SHOUTRRR_FORWARDER_PORT}/xray`` when
   ``SHOUTRRR_URLS`` is set. Failures are swallowed at ``DEBUG`` — a
   crashed forwarder must never break a speed-test or a retest cron.

Subsequent phases emit event names like ``isp.speed_test.result``,
``isp.retest.completed``, ``isp.retest.noop``, ``isp.retest.error``.
Keep names lowercase ``a-z_.`` so they survive Loki/Grafana labels.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Final

import httpx

logger = logging.getLogger(__name__)

_EVENT_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_.]*$")
_HTTP_TIMEOUT: Final[float] = 1.0
_DEFAULT_PORT: Final[int] = 18085


def _shoutrrr_endpoint() -> str:
    port = os.environ.get("SHOUTRRR_FORWARDER_PORT", "").strip() or str(_DEFAULT_PORT)
    return f"http://127.0.0.1:{port}/xray"


def _events_enabled() -> bool:
    return os.environ.get("ISP_EVENTS_ENABLED", "true").strip().lower() != "false"


def emit_event(name: str, payload: dict[str, Any]) -> None:
    """Emit a structured event.

    Safe to call unconditionally — enforces name shape, JSON-serialises
    the payload, and isolates HTTP failures behind DEBUG logs.
    """
    if not _events_enabled():
        return
    if not _EVENT_NAME_RE.match(name):
        logger.warning("events: rejected invalid event name %r", name)
        return
    try:
        body = json.dumps({"event": name, **payload}, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        logger.warning("events: payload not JSON-serialisable for %s: %s", name, exc)
        return

    # Always log to stdout.
    logger.info("event=%s payload=%s", name, body)

    # Optional HTTP fan-out only when shoutrrr forwarder is wired up.
    urls = os.environ.get("SHOUTRRR_URLS", "").strip()
    if not urls:
        return
    try:
        httpx.post(_shoutrrr_endpoint(), content=body, timeout=_HTTP_TIMEOUT)
    except httpx.HTTPError as exc:  # pragma: no cover — network path
        logger.debug("events: shoutrrr POST failed for %s: %s", name, exc)
    except OSError as exc:  # pragma: no cover — connection refused etc.
        logger.debug("events: shoutrrr POST OSError for %s: %s", name, exc)
