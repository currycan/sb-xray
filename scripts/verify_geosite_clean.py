#!/usr/bin/env python3
"""部署前自证:下载 manifest 里的 geosite.dat,验证其满足回国路由的两条硬性要求。

1. ``geosite:cn`` **干净** —— 不含被上游 ``@cn`` 标记的海外 CDN
   (``dl.google.com`` / ``redirector.gvt1.com`` / ``clientservices.googleapis.com``)。
   一旦混入,回国规则 ``geosite:cn → cn-exit-balance`` 会把 Google Play 等海外
   服务送回国内出口,导致地区敏感应用从国内 IP 访问而失效。
2. 服务端用到的**全部 geosite 分类齐全** —— 缺任一分类,对应分流规则(广告
   拦截 / 流媒体 / 回国护栏)会静默失效。

URL 取自 ``sb_xray.geo._MANIFEST``,与运行时同一真源;换源后无需改本脚本。

用法::

    .venv/bin/python scripts/verify_geosite_clean.py

退出码 0 = 通过;1 = 失败(并打印不达标项)。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sb_xray import geo
from sb_xray import http as sbhttp

# 服务端实际引用的 geosite 分类(回国触发器/护栏 + 广告拦截 + 服务级分流)。
# 缺任一项都会让对应路由规则失效。
REQUIRED_CATEGORIES: tuple[str, ...] = (
    "cn",
    "geolocation-!cn",
    "category-ads-all",
    "openai",
    "netflix",
    "disney",
    "anthropic",
    "google",
    "google-gemini",
    "youtube",
    "category-social-media-!cn",
    "tiktok",
    "amazon",
    "paypal",
    "ebay",
)

# 必须**不在** geosite:cn 内的海外 CDN 子串(@cn 污染特征)。
CN_FORBIDDEN: tuple[str, ...] = (
    "dl.google.com",
    "gvt1.com",
    "clientservices.googleapis.com",
)


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while True:
        byte = buf[i]
        result |= (byte & 0x7F) << shift
        i += 1
        if not byte & 0x80:
            return result, i
        shift += 7


def _iter_fields(buf: bytes) -> Iterator[tuple[int, bytes | None]]:
    """逐字段产出 (field_number, length_delimited_payload | None)。"""
    i, n = 0, len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:  # varint
            _, i = _read_varint(buf, i)
            yield field, None
        elif wire == 2:  # length-delimited
            length, i = _read_varint(buf, i)
            yield field, buf[i : i + length]
            i += length
        elif wire == 5:  # 32-bit
            i += 4
            yield field, None
        elif wire == 1:  # 64-bit
            i += 8
            yield field, None
        else:
            raise ValueError(f"unsupported wire type {wire}")


def parse_geosite(data: bytes) -> dict[str, set[str]]:
    """解析 geosite.dat (GeoSiteList{GeoSite{country_code=1, Domain{value=2}=2}=1})。"""
    cats: dict[str, set[str]] = {}
    for field, payload in _iter_fields(data):
        if field != 1 or payload is None:
            continue
        code: str | None = None
        domains: set[str] = set()
        for f2, p2 in _iter_fields(payload):
            if f2 == 1 and p2 is not None:
                code = p2.decode("utf-8", "replace")
            elif f2 == 2 and p2 is not None:
                for f3, p3 in _iter_fields(p2):
                    if f3 == 2 and p3 is not None:
                        domains.add(p3.decode("utf-8", "replace"))
        if code:
            cats[code.lower()] = domains
    return cats


def main() -> int:
    url = geo._MANIFEST["geosite.dat"]
    print(f"下载 geosite.dat: {url}")
    with httpx.Client(
        timeout=90.0, follow_redirects=True, headers={"User-Agent": sbhttp.DEFAULT_UA}
    ) as client:
        resp = client.get(url)
        resp.raise_for_status()
        cats = parse_geosite(resp.content)

    print(f"分类总数: {len(cats)}")
    failures: list[str] = []

    cn = cats.get("cn", set())
    if not cn:
        failures.append("分类 'cn' 缺失")
    for forbidden in CN_FORBIDDEN:
        hit = next((d for d in cn if forbidden in d), None)
        status = f"❌ 仍在({hit})" if hit else "✅ 干净"
        print(f"  cn 不含 {forbidden:32} {status}")
        if hit:
            failures.append(f"geosite:cn 含海外 CDN {forbidden}({hit})")

    for cat in REQUIRED_CATEGORIES:
        present = cat in cats and bool(cats[cat])
        print(f"  分类 {cat:28} {'✅ 存在' if present else '❌ 缺失'}")
        if not present:
            failures.append(f"分类 {cat} 缺失")

    if failures:
        print("\n不达标:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\n通过: geosite:cn 干净且所需分类齐全。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
