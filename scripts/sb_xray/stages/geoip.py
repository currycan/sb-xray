"""GeoIP / GeoSite data refresh (entrypoint.sh §10 等价实现)。

下载工作全部在 :mod:`sb_xray.geo` 里做;这里只负责启动阶段的调用入口
与状态汇报。``on_startup=True`` 让 ``geo`` 模块尊重 7 天新鲜度窗口,
避免重启容器时重下 ~100 MB。
"""

from __future__ import annotations

import logging

from sb_xray import geo

logger = logging.getLogger(__name__)


def update_geo_data() -> int:
    """启动阶段刷新 geo 数据,返回下载失败数 (不抛异常)。"""
    logger.info("更新 GeoIP/GeoSite 数据库")
    failed = geo.refresh(on_startup=True)
    if failed:
        logger.warning("%d 个规则库下载失败,使用旧缓存", failed)
    return failed
