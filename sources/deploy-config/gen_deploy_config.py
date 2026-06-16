#!/usr/bin/env python3
"""gen_deploy_config.py —— 从 .credentials/ 真相源生成 sb-xray 部署配置。

CLI：``python3 sources/deploy-config/gen_deploy_config.py gen|diff ...``。

单一真相源（默认仓库根 ``.credentials/``，``--credentials-dir`` 覆盖）：

* ``node.list``           —— VPS 节点清单，每行 ``<名> <FQDN> <token>``
* ``openwrt-config.env``  —— OpenWrt 回国出口配置的权威版本（KEY=VALUE）

产出（``gen`` 子命令）：

* 每节点 VPS ``.env``      —— 喂给 ``sources/vps/vps-cn-exit-init.sh`` 的 env 接口
* OpenWrt ``config.env``   —— 校验后的设备 config.env（部署改名自 openwrt-config.env）
* 设备 ``nodes.list``      —— 去注释的节点清单（脚本默认查找名为复数 nodes.list）

设计契约：

* 本脚本只**产配置**，不执行任何写操作。apply 复用现有幂等脚本
  （``vps-cn-exit-init.sh`` / ``openwrt-init.sh``）——它们已 compare-then-write。
* **不硬编码任何环境特定值**——全部从 ``.credentials/`` 运行时读取，故脚本本身可进仓。
* ``diff`` 子命令做纯文本比对（期望 vs ``--actual`` 提供的设备现配置），
  网络无关、可单测；SSH 拉取设备现配置由 project-operator skill 流程负责。

secret 纪律：本脚本只搬运真相源里的值到设备配置；不打印 secret 到 stderr/日志，
不把任何值写进 git 跟踪范围（产出默认落 ``.ops/generated/``，已 gitignore）。
"""

from __future__ import annotations

import argparse
import difflib
import sys
from dataclasses import dataclass
from pathlib import Path

# 本脚本在 <repo>/sources/deploy-config/，故 repo 根 = parent.parent.parent
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CREDENTIALS_DIR = REPO_ROOT / ".credentials"
DEFAULT_OPERATOR_CONF = REPO_ROOT / ".ops" / "operator.conf"
DEFAULT_OUT_DIR = REPO_ROOT / ".ops" / "generated"

# 生成 VPS .env 至少需要真相源里的这些键
VPS_ENV_REQUIRED: tuple[str, ...] = ("CN_EXIT_MODE", "TS_EXPECTED_IP")
# OpenWrt config.env 部署前至少应含的键（缺失即拦截，避免带病下发）
OPENWRT_REQUIRED: tuple[str, ...] = (
    "CN_EXIT_MODE",
    "TS_HOSTNAME",
    "TS_EXPECTED_IP",
    "TS_ADVERTISE_ROUTES",
    "PEER_TS_IP",
)
# canary 节点让 watchtower 提前 1h（北京 03:00），与 vps-cn-exit-init.sh §3.5 一致
CANARY_WATCHTOWER_SCHEDULE = "0 0 3 * * *"


class GenError(Exception):
    """真相源缺失/格式错误等可预期的生成失败。"""


@dataclass(frozen=True)
class Node:
    """node.list 中的一台 VPS 节点。"""

    name: str
    fqdn: str
    token: str


def parse_node_list(text: str) -> list[Node]:
    """解析 node.list 文本为节点列表，跳过空行与 ``#`` 注释行。"""
    nodes: list[Node] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            raise GenError(f"node.list 第 {lineno} 行应为 3 列 <名> <FQDN> <token>，实为：{raw!r}")
        nodes.append(Node(name=parts[0], fqdn=parts[1], token=parts[2]))
    if not nodes:
        raise GenError("node.list 未解析出任何节点")
    return nodes


def parse_env(text: str) -> dict[str, str]:
    """解析 KEY=VALUE 文本为字典，跳过空行与注释；值首尾空白去除，保留内部原样。"""
    env: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def _require_keys(env: dict[str, str], keys: tuple[str, ...], source: str) -> None:
    missing = [k for k in keys if not env.get(k)]
    if missing:
        raise GenError(f"{source} 缺少必需键：{', '.join(missing)}")


def render_nodes_list(nodes: list[Node]) -> str:
    """生成设备用 nodes.list：每行 ``<名> <FQDN> <token>``，无注释，结尾换行。"""
    lines = [f"{n.name} {n.fqdn} {n.token}" for n in nodes]
    return "\n".join(lines) + "\n"


def render_vps_env(node: Node, truth: dict[str, str], *, canary: bool) -> str:
    """生成一台 VPS 的 .env 内容（对齐 vps-cn-exit-init.sh 的 env 接口）。

    ``tsip`` 取真相源的 OpenWrt 固定 Tailscale IP（``TS_EXPECTED_IP``），即 socks5 腿回国出口。
    """
    _require_keys(truth, VPS_ENV_REQUIRED, "openwrt-config.env")
    lines = [
        "# 由 sources/deploy-config/gen_deploy_config.py 从 .credentials/ 生成 —— 勿手改，改真相源后重生成。",
        f"# 节点：{node.name}（{node.fqdn}）",
        f"CN_EXIT_MODE={truth['CN_EXIT_MODE']}",
        "ENABLE_REVERSE=true",
        "ENABLE_SOCKS5_PROXY=true",
        f"tsip={truth['TS_EXPECTED_IP']}",
        f"domain={node.fqdn}",
    ]
    if canary:
        lines.append(f"WATCHTOWER_SCHEDULE={CANARY_WATCHTOWER_SCHEDULE}")
    return "\n".join(lines) + "\n"


def render_openwrt_config(truth_text: str, truth: dict[str, str]) -> str:
    """校验 OpenWrt config.env：缺键即报错；通过则原样返回（规范化结尾换行）。

    openwrt-config.env 本身即权威版本，部署只是改名为 config.env，故不重排键，
    只做必需键校验 + 结尾换行规范化，避免无谓 diff 噪声。
    """
    _require_keys(truth, OPENWRT_REQUIRED, "openwrt-config.env")
    return truth_text.rstrip("\n") + "\n"


def diff_configs(expected: str, actual: str, *, label: str) -> list[str]:
    """期望配置 vs 设备现配置的统一 diff 行列表（空列表=无漂移）。"""
    diff = difflib.unified_diff(
        actual.splitlines(keepends=True),
        expected.splitlines(keepends=True),
        fromfile=f"{label} (设备现配置)",
        tofile=f"{label} (期望)",
    )
    return [line.rstrip("\n") for line in diff]


def load_operator_conf(path: Path) -> dict[str, str]:
    """读取 .ops/operator.conf（不存在则返回空字典）。"""
    if not path.is_file():
        return {}
    return parse_env(path.read_text(encoding="utf-8"))


def _read(path: Path) -> str:
    if not path.is_file():
        raise GenError(f"真相源文件不存在：{path}")
    return path.read_text(encoding="utf-8")


def _resolve_canary(args: argparse.Namespace, conf: dict[str, str]) -> str:
    if args.canary:
        return str(args.canary)
    return conf.get("SBX_CANARY_NODE", "")


def cmd_gen(args: argparse.Namespace) -> int:
    cred_dir = Path(args.credentials_dir)
    nodes = parse_node_list(_read(cred_dir / "node.list"))
    truth_text = _read(cred_dir / "openwrt-config.env")
    truth = parse_env(truth_text)
    conf = load_operator_conf(Path(args.operator_conf))
    canary_node = _resolve_canary(args, conf)

    artifacts: dict[str, str] = {}
    target = str(args.target)
    if target in ("vps", "all"):
        selected = [n for n in nodes if n.name == args.node] if args.node else nodes
        if args.node and not selected:
            raise GenError(f"node.list 中无名为 {args.node!r} 的节点")
        for node in selected:
            artifacts[f"vps/{node.name}.env"] = render_vps_env(
                node, truth, canary=(node.name == canary_node)
            )
    if target in ("openwrt", "all"):
        artifacts["openwrt/config.env"] = render_openwrt_config(truth_text, truth)
        artifacts["openwrt/nodes.list"] = render_nodes_list(nodes)

    if args.stdout:
        for rel, content in artifacts.items():
            sys.stdout.write(f"# ===== {rel} =====\n{content}\n")
        return 0

    out_dir = Path(args.out_dir)
    for rel, content in artifacts.items():
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        print(f"[gen] {dest}")
    print(f"[gen] canary 节点：{canary_node or '(未配置 SBX_CANARY_NODE)'}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    cred_dir = Path(args.credentials_dir)
    truth_text = _read(cred_dir / "openwrt-config.env")
    truth = parse_env(truth_text)
    conf = load_operator_conf(Path(args.operator_conf))

    if args.target == "openwrt":
        expected = render_openwrt_config(truth_text, truth)
        label = "openwrt/config.env"
    else:
        nodes = parse_node_list(_read(cred_dir / "node.list"))
        match = [n for n in nodes if n.name == args.node]
        if not match:
            raise GenError(f"node.list 中无名为 {args.node!r} 的节点")
        canary_node = _resolve_canary(args, conf)
        expected = render_vps_env(match[0], truth, canary=(args.node == canary_node))
        label = f"vps/{args.node}.env"

    actual = Path(args.actual).read_text(encoding="utf-8")
    drift = diff_configs(expected, actual, label=label)
    if not drift:
        print(f"[diff] {label} 零漂移 ✅")
        return 0
    print(f"[diff] {label} 有漂移 ⚠️：")
    print("\n".join(drift))
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gen_deploy_config.py",
        description="从 .credentials/ 真相源生成 sb-xray 部署配置（VPS .env / OpenWrt config.env / nodes.list）",
    )
    parser.add_argument("--credentials-dir", default=str(DEFAULT_CREDENTIALS_DIR), help="真相源目录")
    parser.add_argument("--operator-conf", default=str(DEFAULT_OPERATOR_CONF), help=".ops/operator.conf 路径")
    parser.add_argument("--canary", default="", help="覆盖 canary 节点名（默认读 operator.conf 的 SBX_CANARY_NODE）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("gen", help="生成部署配置")
    p_gen.add_argument("--target", choices=("vps", "openwrt", "all"), default="all")
    p_gen.add_argument("--node", default="", help="只生成指定节点的 VPS .env")
    p_gen.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="产出目录")
    p_gen.add_argument("--stdout", action="store_true", help="打印到 stdout 而非写文件")
    p_gen.set_defaults(func=cmd_gen)

    p_diff = sub.add_parser("diff", help="期望配置 vs 设备现配置（只读）")
    p_diff.add_argument("--target", choices=("vps", "openwrt"), default="vps")
    p_diff.add_argument("--node", default="", help="VPS 目标时的节点名")
    p_diff.add_argument("--actual", required=True, help="设备现配置文件（由 skill 经 SSH 拉到本地）")
    p_diff.set_defaults(func=cmd_diff)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "diff" and args.target == "vps" and not args.node:
        parser.error("diff --target vps 需要 --node")
    try:
        result: int = args.func(args)
        return result
    except GenError as exc:
        print(f"[gen-deploy-config] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
