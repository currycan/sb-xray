"""HTTP probing utilities (entrypoint.sh §3 equivalent).

The Bash version shelled out to `curl -I -s --max-time 3 --retry 2`.
We use httpx here, which is already a runtime dependency. Two call
shapes are exposed: sync (`probe` / `trace_url`) for drop-in
replacement inside sequential code paths, and async (`probe_async`)
for Phase 2's concurrent speed test + media probe.
"""

from __future__ import annotations

from typing import Final

import httpx

DEFAULT_UA: Final[str] = "Mozilla/5.0"
PROBE_TIMEOUT: Final[float] = 3.0
TRACE_TIMEOUT: Final[float] = 5.0


def probe(url: str, *, follow: bool = False, timeout: float = PROBE_TIMEOUT) -> str:
    """Issue a HEAD request and return the status code as a string.

    Returns "Timeout" on any network/protocol error (mirrors the Bash
    behavior of returning the literal string "Timeout" from curl errors).
    """
    try:
        with httpx.Client(
            follow_redirects=follow,
            timeout=timeout,
            headers={"User-Agent": DEFAULT_UA},
        ) as client:
            resp = client.head(url)
            return str(resp.status_code)
    except httpx.HTTPError:
        return "Timeout"


async def probe_async(url: str, *, follow: bool = False, timeout: float = PROBE_TIMEOUT) -> str:
    """Async variant of :func:`probe` for use with `asyncio.gather`."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=follow,
            timeout=timeout,
            headers={"User-Agent": DEFAULT_UA},
        ) as client:
            resp = await client.head(url)
            return str(resp.status_code)
    except httpx.HTTPError:
        return "Timeout"


def trace_url(url: str, *, timeout: float = TRACE_TIMEOUT) -> str:
    """Follow redirects and return the final landing URL.

    Returns "" on failure (mirrors the Bash `curl -sSL -w %{url_effective}`
    which emitted nothing when the initial connection failed).
    """
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": DEFAULT_UA},
        ) as client:
            resp = client.head(url)
            return str(resp.url)
    except httpx.HTTPError:
        return ""
