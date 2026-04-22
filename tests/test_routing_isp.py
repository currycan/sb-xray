"""Tests for sb_xray.routing.isp (entrypoint.sh §10 equivalent)."""

from __future__ import annotations

import json

from sb_xray.routing import isp

# ---- process_single_isp -----------------------------------------------------


def test_process_single_isp_xray_json() -> None:
    xray, _ = isp.process_single_isp(
        prefix="CN2",
        ip="192.0.2.10",
        port=12345,
        user="alice",
        password="s3cret",
        tag="proxy-cn2",
    )
    data = json.loads(xray)
    assert data["tag"] == "proxy-cn2"
    assert data["protocol"] == "socks"
    server = data["settings"]["servers"][0]
    assert server["address"] == "192.0.2.10"
    assert server["port"] == 12345
    assert server["users"][0]["user"] == "alice"
    assert server["users"][0]["pass"] == "s3cret"


def test_process_single_isp_singbox_json() -> None:
    _, sb = isp.process_single_isp(
        prefix="CN2",
        ip="192.0.2.10",
        port=12345,
        user="alice",
        password="s3cret",
        tag="proxy-cn2",
    )
    data = json.loads(sb)
    assert data["type"] == "socks"
    assert data["tag"] == "proxy-cn2"
    assert data["server"] == "192.0.2.10"
    assert data["server_port"] == 12345
    assert data["username"] == "alice"
    assert data["password"] == "s3cret"


# ---- build_sb_urltest -------------------------------------------------------


def test_sb_urltest_empty_when_no_nodes() -> None:
    assert isp.build_sb_urltest({}) == ""


def test_sb_urltest_sorted_desc_by_speed() -> None:
    out = isp.build_sb_urltest({"proxy-slow": 5.0, "proxy-fast": 80.0, "proxy-mid": 30.0})
    data = json.loads(out.rstrip(","))  # strip template-splice trailing comma
    assert data["type"] == "urltest"
    assert data["tag"] == "isp-auto"
    assert data["outbounds"] == [
        "proxy-fast",
        "proxy-mid",
        "proxy-slow",
        "direct",
    ]
    # Default probe URL is Cloudflare 1 MiB (Phase 1 of isp-auto optimisation).
    assert data["url"] == "https://speed.cloudflare.com/__down?bytes=1048576"
    assert data["interval"] == "1m"
    assert data["tolerance"] == 300


def test_sb_urltest_honours_env_probe_url(monkeypatch) -> None:
    monkeypatch.setenv("ISP_PROBE_URL", "https://example.test/probe")
    monkeypatch.setenv("ISP_PROBE_INTERVAL", "30s")
    monkeypatch.setenv("ISP_PROBE_TOLERANCE_MS", "450")
    out = isp.build_sb_urltest({"proxy-hk": 120.0})
    data = json.loads(out.rstrip(","))
    assert data["url"] == "https://example.test/probe"
    assert data["interval"] == "30s"
    assert data["tolerance"] == 450


def test_sb_urltest_explicit_probe_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("ISP_PROBE_URL", "https://env.test/probe")
    cfg = isp.ProbeConfig(url="https://kwarg.test/probe", interval="5m", tolerance_ms=900)
    out = isp.build_sb_urltest({"proxy-hk": 120.0}, probe=cfg)
    data = json.loads(out.rstrip(","))
    assert data["url"] == "https://kwarg.test/probe"
    assert data["interval"] == "5m"
    assert data["tolerance"] == 900


def test_sb_urltest_has_trailing_comma_for_template_splice() -> None:
    """Regression: the fragment is spliced between two other JSON
    objects in ``templates/sing-box/sb.json`` (``${SB_CUSTOM_OUTBOUNDS}``
    → ``${SB_ISP_URLTEST}`` → literal block outbound). Without a
    trailing comma, the rendered file has ``}{`` and
    ``json.decoder.JSONDecodeError: Expecting ',' delimiter`` fires —
    exactly the line-44 crash observed on prod."""
    out = isp.build_sb_urltest({"proxy-hk": 120.0})
    assert out.endswith(",")
    # And the payload minus the trailing comma is valid JSON by itself.
    import json as _json

    _json.loads(out.rstrip(","))


def test_sb_urltest_empty_has_no_trailing_comma() -> None:
    """Empty fragment must stay empty — adding a stray "," would break
    sb.json when zero ISP nodes are configured."""
    assert isp.build_sb_urltest({}) == ""


# ---- build_xray_balancer ----------------------------------------------------


def test_xray_balancer_empty_when_no_nodes() -> None:
    obs, bal = isp.build_xray_balancer({})
    assert obs == ""
    assert bal == ""


def test_xray_balancer_selector_sorted_desc() -> None:
    obs, bal = isp.build_xray_balancer({"proxy-cn2": 60.0, "proxy-aws": 80.0})
    assert '"proxy-aws"' in obs and '"proxy-cn2"' in obs
    assert obs.index('"proxy-aws"') < obs.index('"proxy-cn2"')
    assert '"isp-auto"' in bal
    assert '"leastPing"' in bal
    assert '"fallbackTag": "direct"' in bal
    # Default probe URL lives in the observatory fragment.
    assert "speed.cloudflare.com/__down" in obs


def test_xray_balancer_honours_env_probe_url(monkeypatch) -> None:
    monkeypatch.setenv("ISP_PROBE_URL", "https://example.test/probe")
    monkeypatch.setenv("ISP_PROBE_INTERVAL", "2m")
    obs, _ = isp.build_xray_balancer({"proxy-cn2": 60.0})
    # Wrap as an object so we can parse the observatory payload back.
    obs_obj = json.loads("{" + obs.rstrip(",") + "}")
    observatory = obs_obj["observatory"]
    assert observatory["probeUrl"] == "https://example.test/probe"
    assert observatory["probeInterval"] == "2m"


def test_xray_balancer_fragments_roundtrip_as_json() -> None:
    """Regression: ``.strip('{}')`` in the old port ate the inner closing
    brace of the observatory object, producing invalid JSON when the
    fragment was spliced into ``xr.json``."""
    import json as _json

    obs, bal = isp.build_xray_balancer({"proxy-cn2": 60.0, "proxy-aws": 80.0})
    # Fragments always end with a comma (intended for templated splice).
    assert obs.endswith(",")
    assert bal.endswith(",")
    # Wrap back into an object and verify each fragment is syntactically
    # valid JSON on its own.
    wrapped_obs = "{" + obs.rstrip(",") + "}"
    wrapped_bal = "{" + bal.rstrip(",") + "}"
    obs_obj = _json.loads(wrapped_obs)
    bal_obj = _json.loads(wrapped_bal)
    assert "observatory" in obs_obj
    assert "balancers" in bal_obj


# ---- apply_isp_routing_logic: decision branches ----------------------------


def _ctx(**overrides: object) -> isp.RoutingContext:
    base: dict[str, object] = dict(
        ip_type="isp",
        geoip_info="Tokyo JP|203.0.113.1",
        default_isp="",
        direct_speed=50.0,
        fastest_proxy_tag=None,
        proxy_max_speed=0.0,
    )
    base.update(overrides)
    return isp.RoutingContext(**base)  # type: ignore[arg-type]


def test_branch_manual_default_isp() -> None:
    d = isp.apply_isp_routing_logic(_ctx(default_isp="CN2_ISP"))
    assert d.isp_tag == "proxy-cn2"


def test_branch_manual_default_isp_lowercases_and_hyphenates() -> None:
    d = isp.apply_isp_routing_logic(_ctx(default_isp="AWS TOKYO_ISP"))
    assert d.isp_tag == "proxy-aws-tokyo"


def test_branch_restricted_region_with_proxy() -> None:
    d = isp.apply_isp_routing_logic(
        _ctx(
            geoip_info="香港 HK|192.0.2.1",
            fastest_proxy_tag="proxy-aws",
            proxy_max_speed=120.0,
        )
    )
    assert d.isp_tag == "proxy-aws"


def test_branch_restricted_region_no_proxy_fallback_direct() -> None:
    d = isp.apply_isp_routing_logic(
        _ctx(
            geoip_info="中国 CN|192.0.2.5",
            fastest_proxy_tag=None,
        )
    )
    assert d.isp_tag == "direct"


def test_branch_hosting_ip_with_proxy() -> None:
    d = isp.apply_isp_routing_logic(
        _ctx(
            ip_type="hosting",
            fastest_proxy_tag="proxy-cn2",
            proxy_max_speed=30.0,
        )
    )
    assert d.isp_tag == "proxy-cn2"


def test_branch_residential_unrestricted_direct() -> None:
    d = isp.apply_isp_routing_logic(_ctx(ip_type="isp"))
    assert d.isp_tag == "direct"


# ---- apply_isp_routing_logic: IS_8K_SMOOTH ---------------------------------


def test_smooth_true_when_proxy_over_100mbps() -> None:
    d = isp.apply_isp_routing_logic(
        _ctx(
            ip_type="hosting",
            fastest_proxy_tag="proxy-aws",
            proxy_max_speed=150.0,
        )
    )
    assert d.is_8k_smooth is True


def test_smooth_false_when_proxy_under_100mbps() -> None:
    d = isp.apply_isp_routing_logic(
        _ctx(
            ip_type="hosting",
            fastest_proxy_tag="proxy-cn2",
            proxy_max_speed=80.0,
        )
    )
    assert d.is_8k_smooth is False


def test_smooth_uses_direct_speed_when_tag_direct() -> None:
    d = isp.apply_isp_routing_logic(_ctx(direct_speed=150.0))
    assert d.isp_tag == "direct"
    assert d.is_8k_smooth is True


# ---- build_xray_service_rules ----------------------------------------------


def test_service_rules_respects_outbounds() -> None:
    rules = isp.build_xray_service_rules(
        outbounds={"CHATGPT_OUT": "isp-auto", "NETFLIX_OUT": "direct"}
    )
    assert rules.endswith(",")
    assert '"geosite:openai"' in rules
    assert '"balancerTag": "isp-auto"' in rules
    assert '"outboundTag": "direct"' in rules


def test_service_rules_openai_has_marktag() -> None:
    rules = isp.build_xray_service_rules(outbounds={"CHATGPT_OUT": "proxy-cn2"})
    assert '"marktag": "fix_openai"' in rules


def test_service_rules_unknown_defaults_to_direct() -> None:
    rules = isp.build_xray_service_rules(outbounds={})
    assert '"outboundTag": "direct"' in rules
