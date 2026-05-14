"""supervisord eventlistener: log xray exit details for crash diagnosis.

Listens for ``PROCESS_STATE_EXITED`` events and, when ``xray`` exits, emits
a single structured line to stdout (which supervisord captures into the
container log). This lets operators correlate ``EADDRINUSE`` autorestart
loops with the underlying crash cause (e.g. ``exitcode=-9`` → SIGKILL,
likely OOM on small-memory VPS nodes).

Implements the supervisord eventlistener wire protocol:
https://supervisord.org/events.html#event-listener-protocol

Wire format::

    READY\\n                           ← we send when ready for next event
    <header>\\n<payload>               ← supervisord sends event
    RESULT 2\\n{OK|FAIL}               ← we always send a result

We never raise on malformed input — any exception is logged via stderr and
we still return ``OK`` so supervisord doesn't tear us down (which would
itself trigger an autorestart and add noise).
"""

from __future__ import annotations

import sys
from typing import IO

_TARGET_PROCESS = "xray"
_INTERESTING_EVENT = "PROCESS_STATE_EXITED"


def _parse_kv(blob: str) -> dict[str, str]:
    """Parse supervisord's ``key:value key:value`` header / payload."""
    out: dict[str, str] = {}
    for token in blob.strip().split():
        if ":" in token:
            key, _, value = token.partition(":")
            out[key] = value
    return out


def _format_exit_line(payload: dict[str, str]) -> str:
    """Render a single human-readable diagnostic line."""
    expected = payload.get("expected", "?")
    pid = payload.get("pid", "?")
    from_state = payload.get("from_state", "?")
    process = payload.get("processname", "?")
    return (
        f"[xray-exit] processname={process} from_state={from_state} pid={pid} expected={expected}"
    )


def _handle_event(header: dict[str, str], payload_blob: str, out: IO[str]) -> None:
    if header.get("eventname") != _INTERESTING_EVENT:
        return
    payload = _parse_kv(payload_blob)
    if payload.get("processname") != _TARGET_PROCESS:
        return
    out.write(_format_exit_line(payload) + "\n")
    out.flush()


def run(stdin: IO[str] = sys.stdin, stdout: IO[str] = sys.stdout) -> int:
    """Event loop. Returns 0 only on EOF — supervisord normally never closes."""
    while True:
        stdout.write("READY\n")
        stdout.flush()

        header_line = stdin.readline()
        if not header_line:
            return 0
        header = _parse_kv(header_line)
        try:
            payload_len = int(header.get("len", "0"))
        except ValueError:
            payload_len = 0

        payload_blob = stdin.read(payload_len) if payload_len else ""
        try:
            _handle_event(header, payload_blob, stdout)
        except Exception as exc:
            sys.stderr.write(f"[xray-exit] handler error: {exc!r}\n")
            sys.stderr.flush()

        stdout.write("RESULT 2\nOK")
        stdout.flush()
