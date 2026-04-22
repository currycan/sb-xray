"""GeoIP / GeoSite 规则库下载器 (entrypoint.sh §10 等价实现)。

完成三件事:
1. 从 Loyalsoldier / chocolate4u / runetfreedom 并行下载 6 个 ``.dat``
   规则库到持久化目录 ``/geo``(docker-compose 挂载为 ``./geo:/geo``),
   避免容器每次重启重下 ~100 MB。
2. 在 ``/usr/local/bin/*.dat`` 维护符号链接指向 ``/geo/*.dat``,
   保持 xray / sing-box 的查找路径稳定。
3. cron 场景触发时通过 ``supervisorctl`` 重启 xray 以加载新规则;
   启动场景 (supervisord 尚未起) 自动跳过重启。

设计要点:
- ``os.replace(tmp, final)`` 原子替换,下载中断不会污染已有文件。
- 单文件失败仅 WARN,不中断其他文件 (陈旧缓存好过启动失败)。
- ``refresh(on_startup=True)`` 检测文件 <7 天则跳过下载,靠 cron
  每天 03:00 保持新鲜度 (首次冷启动 ``/geo`` 目录空,仍全量下载)。

历史注记: 原本此处有 MPH 缓存重建逻辑 (PR #5505 的 ``buildMphCache``
CLI)。该特性在 2026-04-13 被 PR #5814 的 geodata refactor 整体 revert,
新方案运行时自动生效,无需重建缓存。详见
``docs/10-implementation-notes.md §M1-4``。
"""

from __future__ import annotations

import concurrent.futures as cf
import contextlib
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Final

import httpx

from sb_xray import http as sbhttp

logger = logging.getLogger(__name__)

GEO_DIR: Final[Path] = Path("/geo")
# xray 把 .dat 从其二进制所在目录 (``/usr/local/bin/bin``) 读取;
# sing-box 与历史脚本从 ``/usr/local/bin`` 读。两处都要维护符号链接,
# 缺一都会触发 "open geoip.dat: no such file or directory"。
LINK_DIRS: Final[tuple[Path, ...]] = (
    Path("/usr/local/bin/bin"),
    Path("/usr/local/bin"),
)
LINK_DIR: Final[Path] = LINK_DIRS[0]  # 保留单目录常量,向后兼容旧调用
MAX_AGE_SECONDS: Final[float] = 7 * 24 * 3600  # <7 天视为新鲜
TIMEOUT: Final[float] = 60.0  # 单文件 10-30 MB,给 60s 兜底
CHUNK_SIZE: Final[int] = 65536
_SUPERVISOR_SOCKET: Final[Path] = Path("/var/run/supervisor.sock")

_MANIFEST: Final[dict[str, str]] = {
    "geoip.dat": "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat",
    "geosite.dat": "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat",
    "geoip_IR.dat": "https://github.com/chocolate4u/Iran-v2ray-rules/releases/latest/download/geoip.dat",
    "geosite_IR.dat": "https://github.com/chocolate4u/Iran-v2ray-rules/releases/latest/download/geosite.dat",
    "geoip_RU.dat": "https://github.com/runetfreedom/russia-v2ray-rules-dat/releases/latest/download/geoip.dat",
    "geosite_RU.dat": "https://github.com/runetfreedom/russia-v2ray-rules-dat/releases/latest/download/geosite.dat",
}


def _is_fresh(path: Path, max_age: float) -> bool:
    return path.is_file() and (time.time() - path.stat().st_mtime) < max_age


def _download_one(name: str, url: str, target_dir: Path, timeout: float) -> bool:
    final = target_dir / name
    tmp = target_dir / f".{name}.tmp"
    try:
        with (
            httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": sbhttp.DEFAULT_UA},
            ) as client,
            client.stream("GET", url) as resp,
        ):
            resp.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=CHUNK_SIZE):
                    fh.write(chunk)
        os.replace(tmp, final)
        return True
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("%s 下载失败: %s", name, exc)
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        return False


def _refresh_symlinks(target_dir: Path, link_dirs: tuple[Path, ...]) -> None:
    """为每个 ``link_dirs/<name>.dat`` 重建指向 ``target_dir/<name>.dat`` 的符号链接。"""
    for link_dir in link_dirs:
        link_dir.mkdir(parents=True, exist_ok=True)
        for dat in target_dir.glob("*.dat"):
            link = link_dir / dat.name
            try:
                if link.is_symlink() or link.exists():
                    link.unlink()
                link.symlink_to(dat)
            except OSError as exc:
                logger.warning("符号链接 %s 失败: %s", link, exc)


def _restart_xray_if_running(
    *,
    socket_path: Path = _SUPERVISOR_SOCKET,
    runner: object = subprocess,
) -> None:
    """只在 supervisord 存活时重启 xray,启动阶段自动无操作。"""
    if not socket_path.is_socket():
        return
    try:
        runner.run(  # type: ignore[attr-defined]
            ["supervisorctl", "stop", "xray"],
            check=False,
            timeout=10,
        )
        for stale in Path("/dev/shm").glob("uds*"):
            with contextlib.suppress(OSError):
                stale.unlink()
        runner.run(  # type: ignore[attr-defined]
            ["supervisorctl", "start", "xray"],
            check=False,
            timeout=10,
        )
        logger.info("xray 已重启以加载新规则")
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("重启 xray 失败: %s", exc)


def refresh(
    *,
    on_startup: bool,
    target_dir: Path = GEO_DIR,
    link_dir: Path | None = None,
    link_dirs: tuple[Path, ...] | None = None,
    max_age: float = MAX_AGE_SECONDS,
    timeout: float = TIMEOUT,
    manifest: dict[str, str] | None = None,
) -> int:
    """下载缺失或过期的规则库,返回失败数 (0 = 全部就绪)。

    - ``on_startup=True``: 由 entrypoint 启动阶段调用。文件全部 <max_age
      秒时跳过下载;不触发 xray 重启 (supervisord 还没起)。
    - ``on_startup=False``: 由 cron (``entrypoint.py geo-update``) 调用。
      一律重新下载;下载成功且 supervisord 活跃时重启 xray。

    ``link_dir`` 作为单目录老接口仍受支持 (测试用);正常部署通过
    ``link_dirs`` 传入多目录 (默认 ``LINK_DIRS`` = xray + sing-box 各一)。
    """
    if link_dirs is None:
        link_dirs = (link_dir,) if link_dir is not None else LINK_DIRS
    files = manifest if manifest is not None else _MANIFEST
    target_dir.mkdir(parents=True, exist_ok=True)

    if on_startup:
        to_fetch = {
            name: url for name, url in files.items() if not _is_fresh(target_dir / name, max_age)
        }
        if not to_fetch:
            logger.info("全部 .dat 文件 <7 天,跳过下载")
            _refresh_symlinks(target_dir, link_dirs)
            return 0
    else:
        to_fetch = dict(files)

    logger.info("开始下载 %d/%d 个规则库 → %s", len(to_fetch), len(files), target_dir)
    with cf.ThreadPoolExecutor(max_workers=max(1, len(to_fetch))) as pool:
        futures = {
            pool.submit(_download_one, name, url, target_dir, timeout): name
            for name, url in to_fetch.items()
        }
        results = [fut.result() for fut in cf.as_completed(futures)]

    _refresh_symlinks(target_dir, link_dirs)
    failed = results.count(False)
    ok = len(results) - failed
    logger.info("下载完成: 成功 %d 失败 %d", ok, failed)

    if not on_startup and failed == 0:
        _restart_xray_if_running()
    return failed
