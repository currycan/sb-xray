"""Tests for scripts/sb_xray/substore_check.py (daily sub fetch health check)."""

from __future__ import annotations

import httpx
from sb_xray import substore_check as sc


def _subs_payload() -> dict:
    return {
        "status": "success",
        "data": [
            {"name": "ssrdog", "source": "remote", "proxy": "socks5://1.2.3.4:7891"},
            {"name": "node-jp", "source": "remote", "proxy": ""},
            {"name": "node-fmt", "source": "remote"},
            {"name": "manual-local", "source": "local"},
        ],
    }


def test_list_remote_subs_filters_local_and_malformed():
    subs = sc._list_remote_subs(_subs_payload())
    names = [s["name"] for s in subs]
    assert names == ["ssrdog", "node-jp", "node-fmt"]  # local dropped


def test_list_remote_subs_handles_bad_shape():
    assert sc._list_remote_subs({}) == []
    assert sc._list_remote_subs({"data": "nope"}) == []
    assert sc._list_remote_subs({"data": [None, 1, "x"]}) == []


def test_is_airport_only_when_proxy_set():
    assert sc._is_airport({"proxy": "socks5://1.2.3.4:7891"}) is True
    assert sc._is_airport({"proxy": ""}) is False
    assert sc._is_airport({"proxy": None}) is False
    assert sc._is_airport({}) is False


def test_classify_non_2xx_is_failure():
    r = sc._classify("x", True, 403, None)
    assert r.ok is False
    assert r.reason == "HTTP 403"


def test_classify_zero_nodes_is_failure():
    r = sc._classify("x", False, 200, [])
    assert r.ok is False
    assert r.reason == "0 节点"


def test_classify_ok_counts_nodes():
    r = sc._classify("x", True, 200, [{"name": "a"}, {"name": "b"}])
    assert r.ok is True
    assert r.reason == ""
    assert r.node_count == 2


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")


def test_check_all_mixes_ok_and_failures():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/subs":
            return httpx.Response(200, json=_subs_payload())
        # /download/<name>
        name = request.url.path.rsplit("/", 1)[-1]
        if name == "ssrdog":
            return httpx.Response(200, json=[{"name": "n1"}] * 28)  # airport ok
        if name == "node-jp":
            return httpx.Response(403)  # blocked
        if name == "node-fmt":
            return httpx.Response(200, json=[])  # 0 nodes (token expired)
        return httpx.Response(404)

    results = sc.check_all(api_base="http://t", client=_mock_client(handler))
    by_name = {r.name: r for r in results}
    assert set(by_name) == {"ssrdog", "node-jp", "node-fmt"}
    assert by_name["ssrdog"].ok is True and by_name["ssrdog"].is_airport is True
    assert by_name["ssrdog"].node_count == 28
    assert by_name["node-jp"].ok is False and by_name["node-jp"].reason == "HTTP 403"
    assert by_name["node-fmt"].ok is False and by_name["node-fmt"].reason == "0 节点"


def test_check_all_url_encodes_non_ascii_names():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/subs":
            return httpx.Response(
                200,
                json={"data": [{"name": "优速通", "source": "remote", "proxy": "socks5://x:1"}]},
            )
        seen.append(request.url.path)
        return httpx.Response(200, json=[{"name": "n"}])

    sc.check_all(api_base="http://t", client=_mock_client(handler))
    # the raw (decoded) path that httpx exposes should carry the name
    assert any("优速通" in p for p in seen)


def test_check_all_subs_endpoint_error_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    assert sc.check_all(api_base="http://t", client=_mock_client(handler)) == []


def test_produce_request_exception_is_a_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/subs":
            return httpx.Response(200, json={"data": [{"name": "x", "source": "remote"}]})
        raise httpx.ConnectError("boom")

    results = sc.check_all(api_base="http://t", client=_mock_client(handler))
    assert len(results) == 1
    assert results[0].ok is False
    assert "请求异常" in results[0].reason


def test_run_check_emits_event_only_on_failure(monkeypatch):
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(sc, "emit_event", lambda name, payload: emitted.append((name, payload)))

    monkeypatch.setattr(
        sc,
        "check_all",
        lambda **_: [
            sc.SubResult("ssrdog", True, False, "HTTP 403", 0),
            sc.SubResult("node-jp", False, True, "", 50),
        ],
    )
    rc = sc.run_check_and_report()
    assert rc == 0
    assert len(emitted) == 1
    name, payload = emitted[0]
    assert name == "substore.sub_fetch.failed"
    assert payload["failed"] == 1
    assert payload["total"] == 2
    assert payload["items"] == [{"name": "ssrdog", "airport": True, "reason": "HTTP 403"}]


def test_run_check_silent_when_all_ok(monkeypatch):
    emitted: list[object] = []
    monkeypatch.setattr(sc, "emit_event", lambda *a: emitted.append(a))
    monkeypatch.setattr(sc, "check_all", lambda **_: [sc.SubResult("a", False, True, "", 10)])
    assert sc.run_check_and_report() == 0
    assert emitted == []


def test_run_check_silent_when_no_remote_subs(monkeypatch):
    emitted: list[object] = []
    monkeypatch.setattr(sc, "emit_event", lambda *a: emitted.append(a))
    monkeypatch.setattr(sc, "check_all", lambda **_: [])
    assert sc.run_check_and_report() == 0
    assert emitted == []
