"""GeoIP / GeoSite data refresh (entrypoint.sh §10 等价实现)。

下载工作全部在 :mod:`sb_xray.geo` 里做;这里只负责启动阶段的调用入口
与状态汇报。``on_startup=True`` 让 ``geo`` 模块尊重 7 天新鲜度窗口,
避免重启容器时重下 ~100 MB。
"""

from __future__ import annotations

from sb_xray import geo
from sb_xray import logging as sblog


def update_geo_data() -> int:
    """启动阶段刷新 geo 数据,返回下载失败数 (不抛异常)。"""
    sblog.log("INFO", "[geoip] 更新 GeoIP/GeoSite 数据库")
    failed = geo.refresh(on_startup=True)
    if failed:
        sblog.log("WARN", f"[geoip] {failed} 个规则库下载失败,使用旧缓存")
    return failed
