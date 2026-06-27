"""Daily Sub-Store subscription fetch health check.

Enumerates every *remote* subscription via the Sub-Store backend API
(``GET /api/subs``), then asks Sub-Store to produce each one
(``GET /download/<name>?target=JSON``) — which triggers the real remote
fetch through that subscription's own proxy / User-Agent. A subscription
is considered failed when the produce returns a non-2xx status or zero
nodes (e.g. an airport whose token expired returns 200 + empty list).

Only failures matter: a fully healthy run emits nothing. When at least
one subscription fails, a single ``substore.sub_fetch.failed`` event is
emitted so it surfaces as a readable shoutrrr / Telegram alert
(formatter lives in :mod:`sb_xray.shoutrrr`).

Wired as the ``substore-check`` entrypoint subcommand, run daily by the
crontab installed in :mod:`sb_xray.stages.cron`.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Final, NamedTuple
from urllib.parse import quote

import httpx

from sb_xray.events import emit_event

logger = logging.getLogger(__name__)

_DEFAULT_API_BASE: Final[str] = "http://127.0.0.1:3000"
_PRODUCE_TARGET: Final[str] = "JSON"
_HTTP_TIMEOUT: Final[float] = 30.0
_MAX_CONCURRENCY: Final[int] = 6  # J5: 并发上限，封顶总墙钟 ≈ ceil(N/6)×30s


class SubResult(NamedTuple):
    name: str
    is_airport: bool
    ok: bool
    reason: str  # "" when ok
    node_count: int


def _list_remote_subs(subs_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Pick the URL-fetched (``source == "remote"``) subs from /api/subs."""
    data = subs_payload.get("data") if isinstance(subs_payload, dict) else None
    if not isinstance(data, list):
        return []
    return [s for s in data if isinstance(s, dict) and s.get("source") == "remote"]


def _is_airport(sub: dict[str, Any]) -> bool:
    """Airport subs are the ones we gave a fetch ``proxy`` (国内出口)."""
    proxy = sub.get("proxy")
    return isinstance(proxy, str) and proxy.strip() != ""


def _classify(name: str, is_airport: bool, status: int, json_body: object) -> SubResult:
    if not 200 <= status < 300:
        return SubResult(name, is_airport, False, f"HTTP {status}", 0)
    count = len(json_body) if isinstance(json_body, list) else 0
    if count == 0:
        return SubResult(name, is_airport, False, "0 节点", 0)
    return SubResult(name, is_airport, True, "", count)


def _produce_one(client: httpx.Client, api_base: str, name: str, is_airport: bool) -> SubResult:
    url = f"{api_base}/download/{quote(name, safe='')}"
    try:
        resp = client.get(url, params={"target": _PRODUCE_TARGET})
    except httpx.HTTPError as exc:
        return SubResult(name, is_airport, False, f"请求异常: {type(exc).__name__}", 0)
    body: object = None
    if 200 <= resp.status_code < 300:
        try:
            body = resp.json()
        except ValueError:
            body = None
    return _classify(name, is_airport, resp.status_code, body)


def check_all(
    *,
    api_base: str | None = None,
    client: httpx.Client | None = None,
    max_concurrency: int = _MAX_CONCURRENCY,
) -> list[SubResult]:
    """Produce every remote sub once and return per-sub results (concurrent).

    Uses :class:`~concurrent.futures.ThreadPoolExecutor` with up to
    *max_concurrency* workers (default :data:`_MAX_CONCURRENCY` = 6).
    Total wall-clock time ≈ ceil(N / max_concurrency) × 30 s.

    Returns all results (ok + failed) in input order; callers filter.
    Returns ``[]`` if the subs listing endpoint itself is unreachable / errors.
    """
    api_base = (api_base or os.environ.get("SUB_STORE_API_BASE") or _DEFAULT_API_BASE).rstrip("/")
    own_client = client is None
    if client is None:
        client = httpx.Client(timeout=_HTTP_TIMEOUT)
    try:
        try:
            resp = client.get(f"{api_base}/api/subs")
            resp.raise_for_status()
            subs = _list_remote_subs(resp.json())
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("substore-check: 列举订阅失败 (%s)", type(exc).__name__)
            return []
        names_airports = [(str(sub.get("name") or ""), _is_airport(sub)) for sub in subs]
        names_airports = [(n, a) for n, a in names_airports if n]
        if not names_airports:
            return []
        workers = max(1, min(max_concurrency, len(names_airports)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # pool.map preserves input order — SubResult sequence is stable
            results = list(
                pool.map(
                    lambda na: _produce_one(client, api_base, na[0], na[1]),
                    names_airports,
                )
            )
        return results
    finally:
        if own_client:
            client.close()


def run_check_and_report(*, api_base: str | None = None) -> int:
    """Run the check and emit one event if any subscription failed."""
    results = check_all(api_base=api_base)
    if not results:
        logger.warning("substore-check: 没有可检查的 remote 订阅")
        return 0
    failures = [r for r in results if not r.ok]
    total = len(results)
    if not failures:
        logger.info("substore-check: 全部 %d 条订阅拉取正常", total)
        return 0
    logger.warning("substore-check: %d/%d 条订阅拉取失败", len(failures), total)
    emit_event(
        "substore.sub_fetch.failed",
        {
            "failed": len(failures),
            "total": total,
            "items": [
                {"name": r.name, "airport": r.is_airport, "reason": r.reason} for r in failures
            ],
        },
    )
    return 0
