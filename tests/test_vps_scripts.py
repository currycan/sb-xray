"""sources/vps 下 shell 脚本的行为测试。

覆盖：
1. POSIX 语法（sh -n）；
2. cn-exit-watchdog 并发硬化：非阻塞 flock 自锁（缺 flock 优雅降级）+ tmp+mv 原子状态写。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_VPS = Path(__file__).resolve().parent.parent / "sources" / "vps"
_WATCHDOG = _VPS / "cn-exit-watchdog.sh"


def test_watchdog_posix_syntax_ok() -> None:
    proc = subprocess.run(["sh", "-n", str(_WATCHDOG)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_watchdog_uses_nonblocking_flock_with_degrade() -> None:
    """cron 每分钟一调，必须非阻塞自锁防并发改写 state；缺 flock 不得硬退（优雅降级）。"""
    src = _WATCHDOG.read_text(encoding="utf-8")
    assert "flock -n" in src, "并发去重须用非阻塞 flock -n（已持锁则本轮直接退出）"
    assert "command -v flock" in src, "须探测 flock 是否存在以便缺失时降级"
    # 缺 flock 的分支不得 exit 非零——继续裸跑而非中断告警链路
    assert "WD_LOCK" in src, "应有独立锁文件变量 WD_LOCK"


def test_watchdog_atomic_state_write() -> None:
    """state 写必须 tmp+mv 原子落盘，杜绝并发读到截断的『0 0』半行。"""
    src = _WATCHDOG.read_text(encoding="utf-8")
    assert "_state_write" in src, "状态写须收口到 _state_write helper"
    assert 'mv -f "$_tmp" "$WD_STATE"' in src, "须用 mv 原子替换 state 文件"
    # 禁止裸 echo 直写 state（两处旧写法都要消除）
    assert 'echo "0 0" > "$WD_STATE"' not in src, "恢复路径不得裸写 state"
    assert 'echo "$fails $alerted" > "$WD_STATE"' not in src, "失败路径不得裸写 state"
