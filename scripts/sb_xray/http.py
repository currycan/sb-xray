"""HTTP probing utilities (entrypoint.sh §3 equivalent).

The Bash version shelled out to `curl -I -s --max-time 3 --retry 2`.
We use httpx here, which is already a runtime dependency. Two call
shapes are exposed: sync (`probe` / `trace_url`) for drop-in
replacement inside sequential code paths, and async (`probe_async`)
for Phase 2's concurrent speed test + media probe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import httpx

DEFAULT_UA: Final[str] = "Mozilla/5.0"
PROBE_TIMEOUT: Final[float] = 3.0
TRACE_TIMEOUT: Final[float] = 5.0
GET_TIMEOUT: Final[float] = 6.0
# Read at most this many bytes of a response body — enough to fingerprint
# an unlock/block page, bounded so a large stream can't blow up memory.
_MAX_BODY_BYTES: Final[int] = 64 * 1024


@dataclass(frozen=True)
class FetchResult:
    """Outcome of a body-reading GET probe.

    ``status`` is the (post-redirect) HTTP status code, or ``-1`` on any
    network/protocol failure. ``body`` is the decoded response text,
    truncated to ~64 KiB. ``final_url`` is the URL after redirects.
    ``truncated`` is ``True`` when the body hit the ``_MAX_BODY_BYTES`` cap
    and was cut off — callers should treat an unmatched marker as
    inconclusive rather than a definitive absence.
    """

    status: int
    body: str
    final_url: str
    truncated: bool = False


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


def fetch(url: str, *, follow: bool = True, timeout: float = GET_TIMEOUT) -> FetchResult:
    """GET ``url`` and return status + (truncated) body + final URL.

    Unlike :func:`probe` (HEAD only, status code), this reads the response
    body so callers can tell a real page from a 200-but-blocked page
    (captcha / geo-gate). Streams and stops at ``_MAX_BODY_BYTES``. Any
    network/protocol error yields ``FetchResult(-1, "", "")``.
    """
    try:
        with (
            httpx.Client(
                follow_redirects=follow,
                timeout=timeout,
                headers={"User-Agent": DEFAULT_UA},
            ) as client,
            client.stream("GET", url) as resp,
        ):
            chunks: list[bytes] = []
            total = 0
            truncated = False
            for chunk in resp.iter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total >= _MAX_BODY_BYTES:
                    truncated = True
                    break
            raw = b"".join(chunks)[:_MAX_BODY_BYTES]
            body = raw.decode(resp.encoding or "utf-8", errors="replace")
            return FetchResult(
                status=resp.status_code, body=body, final_url=str(resp.url), truncated=truncated
            )
    except httpx.HTTPError:
        return FetchResult(status=-1, body="", final_url="")


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
