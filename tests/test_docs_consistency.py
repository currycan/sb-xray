"""文档一致性回归:readme/CLAUDE.md 索引与文件系统、entrypoint 子命令保持同步。"""
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


def test_readme_indexes_docs_10_and_11() -> None:
    readme = _read("readme.md")
    # 文件系统里存在 10/11,readme 文档全集表必须可达,否则索引相对文件系统陈旧(C8)。
    assert "./docs/10-multi-wan-leak-prevention.md" in readme
    assert "./docs/11-openwrt-rebuild-and-cutover.md" in readme
