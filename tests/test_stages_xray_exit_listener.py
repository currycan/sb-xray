"""Tests for scripts/sb_xray/stages/xray_exit_listener.py."""

from __future__ import annotations

import io

from sb_xray.stages import xray_exit_listener as xel


def _make_event(eventname: str, payload: str) -> str:
    """Construct a supervisord event frame: header line + payload."""
    payload_len = len(payload)
    header = (
        f"ver:3.0 server:supervisor serial:1 pool:listener "
        f"poolserial:1 eventname:{eventname} len:{payload_len}\n"
    )
    return header + payload


def _drive(input_str: str) -> str:
    """Run the listener over a single event then EOF; return stdout."""
    stdin = io.StringIO(input_str)
    stdout = io.StringIO()
    rc = xel.run(stdin=stdin, stdout=stdout)
    assert rc == 0
    return stdout.getvalue()


def test_listener_emits_diagnostics_for_xray_exit() -> None:
    payload = "processname:xray groupname:xray from_state:RUNNING expected:0 pid:42"
    out = _drive(_make_event("PROCESS_STATE_EXITED", payload))

    # Loop runs until EOF, so output is: READY → diag line → RESULT → READY (next iter).
    assert out.startswith("READY\n")
    assert "RESULT 2\nOK" in out

    # Diagnostic line includes all interesting fields.
    assert "[xray-exit]" in out
    assert "processname=xray" in out
    assert "from_state=RUNNING" in out
    assert "expected=0" in out
    assert "pid=42" in out


def test_listener_ignores_non_xray_exit() -> None:
    payload = "processname:nginx groupname:nginx from_state:RUNNING expected:0 pid:99"
    out = _drive(_make_event("PROCESS_STATE_EXITED", payload))

    assert "READY\n" in out
    assert "RESULT 2\nOK" in out
    assert "[xray-exit]" not in out


def test_listener_ignores_other_event_types() -> None:
    payload = "processname:xray from_state:STARTING"
    out = _drive(_make_event("PROCESS_STATE_RUNNING", payload))

    assert "RESULT 2\nOK" in out
    assert "[xray-exit]" not in out


def test_listener_returns_zero_on_eof() -> None:
    """Empty stdin (supervisord shutting us down) → clean exit."""
    stdin = io.StringIO("")
    stdout = io.StringIO()
    assert xel.run(stdin=stdin, stdout=stdout) == 0
    # We still wrote READY before the EOF read.
    assert stdout.getvalue() == "READY\n"


def test_listener_survives_malformed_payload(monkeypatch) -> None:
    """Even a thrown handler exception must not crash the loop."""

    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("simulated handler failure")

    monkeypatch.setattr(xel, "_handle_event", boom)

    out = _drive(_make_event("PROCESS_STATE_EXITED", "processname:xray"))
    assert "RESULT 2\nOK" in out  # listener still acks


def test_parse_kv_handles_extra_whitespace() -> None:
    parsed = xel._parse_kv("  key1:val1   key2:val2  \n")
    assert parsed == {"key1": "val1", "key2": "val2"}


def test_format_exit_line_falls_back_when_fields_missing() -> None:
    line = xel._format_exit_line({"processname": "xray"})
    assert "processname=xray" in line
    assert "expected=?" in line
    assert "pid=?" in line
    assert "from_state=?" in line
