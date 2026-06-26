"""文档一致性回归:readme/CLAUDE.md 索引与文件系统、entrypoint 子命令保持同步。"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


def test_readme_indexes_docs_10_and_11() -> None:
    readme = _read("readme.md")
    # 文件系统里存在 10/11,readme 文档全集表必须可达,否则索引相对文件系统陈旧(C8)。
    assert "./docs/10-multi-wan-leak-prevention.md" in readme
    assert "./docs/11-openwrt-rebuild-and-cutover.md" in readme


def test_claudemd_doc_range_covers_10_11() -> None:
    claudemd = _read("CLAUDE.md")
    # 文件系统已有 10/11,CLAUDE.md 权威范围声明不得停留在 00–09(C8)。
    assert "00–09" not in claudemd, "CLAUDE.md 仍声明范围 00–09,与 docs/10、11 不符"
    assert "00–11" in claudemd


def _registered_subcommands() -> set[str]:
    src = _read("scripts/entrypoint.py")
    # 解析 sub.add_parser("name", ...) 的首参,即真实注册的子命令集。
    return set(re.findall(r'add_parser\(\s*"([a-z0-9-]+)"', src))


def test_claudemd_lists_all_entrypoint_subcommands() -> None:
    claudemd = _read("CLAUDE.md")
    subs = _registered_subcommands() - {"run"}  # run 单独描述,非"非 run 子命令"
    missing = sorted(s for s in subs if f"`{s}`" not in claudemd)
    assert not missing, f"CLAUDE.md 架构段漏列子命令: {missing}"


def test_doc04_enable_xui_row_notes_compose_false() -> None:
    lines = _read("docs/04-ops-and-troubleshooting.md").splitlines()
    # 定位 env 主表中含 ENABLE_XUI 且含降载开关默认值的那一行(:301 区)。
    row = next(
        ln for ln in lines
        if "ENABLE_XUI" in ln and "Dockerfile ENV" in ln and "ENABLE_SHOUTRRR" in ln
    )
    # 该行必须同时点明 ENABLE_XUI 在 compose 默认 false,消除与 :27/:1090 的张力(C9)。
    assert "compose" in row and "false" in row.lower()


def test_doc04_default_isp_matches_dockerfile_env() -> None:
    dockerfile = _read("Dockerfile")
    speed = _read("scripts/sb_xray/speed_test.py")
    doc04 = _read("docs/04-ops-and-troubleshooting.md")
    # 镜像默认锁 LA_ISP(Dockerfile),但代码读取兜底为空=auto-detect(speed_test)——有意分歧。
    assert 'ENV DEFAULT_ISP="LA_ISP"' in dockerfile
    assert 'os.environ.get("DEFAULT_ISP", "")' in speed
    # docs/04 必须同时讲清:Dockerfile 默认 LA_ISP + 显式置空=测速自动选路。
    isp_row = next(ln for ln in doc04.splitlines() if "`DEFAULT_ISP`" in ln and "LA_ISP" in ln)
    assert "显式置空" in isp_row and "Dockerfile 默认" in isp_row
