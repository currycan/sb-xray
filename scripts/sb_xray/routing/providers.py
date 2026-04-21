"""Port of ``entrypoint.sh:generateProxyProvidersConfig`` (L1011-1075).

Reads ``${WORKDIR}/providers`` (Clash-style YAML snippets of the form
``  Name: {<<: *BaseProvider, url: "..."}``) and merges them with the
``PROVIDERS`` env var (``"Name|URL|Remark"`` pipe-separated), then
writes the four export vars the nginx/client-template layer expects:

* ``CLASH_PROXY_PROVIDERS`` — raw Clash YAML block (post-substitution)
* ``SURGE_PROXY_PROVIDERS`` — Surge Policy-Path line(s), only for
  provider named ``AllOne``
* ``SURGE_PROVIDER_NAMES``  — ``", AllOne"`` or ``""``
* ``STASH_PROVIDER_NAMES``  — ``"AllOne, ..."`` comma+space separated

Behavior is byte-aligned with Bash so ``envsubst`` template rendering
produces identical output.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_URL_LINE_RE = re.compile(r'url:\s*"([^"]+)"')


def _read_provider_file(path: Path) -> str:
    """Return file content with comments / blanks / YAML headers stripped.

    Mirrors ``sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d'
                -e '/^providers:/d' -e '/^proxy-providers:/d'``.
    """
    if not path.is_file():
        return ""
    kept: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped in ("providers:", "proxy-providers:"):
            continue
        if raw.startswith("providers:") or raw.startswith("proxy-providers:"):
            continue
        kept.append(raw)
    return "\n".join(kept)


def _parse_env_providers(raw: str) -> str:
    """Convert ``Name|URL|Remark|Name2|URL2|...`` → Clash YAML lines.

    Bash uses ``awk -F'|'`` with ``NF>=2`` so each triple must have at
    least name+URL; remark is optional. Pipe-separated multiple entries
    appear as consecutive triples on a single line, so we walk in 3-tuples.
    """
    if not raw:
        return ""
    parts = raw.split("|")
    lines: list[str] = []
    i = 0
    while i + 1 < len(parts):
        name = parts[i].strip()
        url = parts[i + 1].strip()
        remark = parts[i + 2].strip() if i + 2 < len(parts) else ""
        if name and url:
            suffix = f" [{remark}]" if remark else ""
            lines.append(
                f'  {name}: {{<<: *BaseProvider, url: "{url}", '
                f'override: {{additional-prefix: "[{name}] ", '
                f'additional-suffix: "{suffix}"}}}}'
            )
        i += 3
    return "\n".join(lines)


def _extract_allone_url(clash_block: str) -> str | None:
    """Find the ``AllOne`` provider's URL in a Clash YAML block."""
    for line in clash_block.splitlines():
        name = line.split(":", 1)[0].strip()
        if name != "AllOne":
            continue
        m = _URL_LINE_RE.search(line)
        if m:
            return m.group(1)
    return None


def _surge_url(url: str) -> str:
    """Replace trailing ``-Common`` or ``-common`` with ``-Surge``."""
    if url.endswith("-Common"):
        return url[: -len("-Common")] + "-Surge"
    if url.endswith("-common"):
        return url[: -len("-common")] + "-Surge"
    return url


def _provider_names(clash_block: str) -> list[str]:
    """Extract unique provider names from the Clash YAML block."""
    names: list[str] = []
    for line in clash_block.splitlines():
        if ":" not in line:
            continue
        name = line.split(":", 1)[0].strip()
        if not name or name.startswith("#"):
            continue
        if name in names:
            continue
        names.append(name)
    return names


def generate_and_export(*, workdir: Path | None = None) -> dict[str, str]:
    """Compute and ``os.environ``-export the four provider env vars.

    ``workdir`` defaults to ``Path(os.environ["WORKDIR"])``. Returns the
    dict of values set, useful for tests / logging.
    """
    if workdir is None:
        workdir = Path(os.environ.get("WORKDIR", "/tmp/sb-xray"))

    provider_file = workdir / "providers"
    clash_providers = _read_provider_file(provider_file)

    env_block = _parse_env_providers(os.environ.get("PROVIDERS", ""))
    if env_block:
        clash_providers = f"{clash_providers}\n{env_block}" if clash_providers else env_block

    surge_providers = ""
    allone_url = _extract_allone_url(clash_providers) if clash_providers else None
    if allone_url:
        surge_url = _surge_url(allone_url)
        surge_providers = (
            f"AllOne = smart, policy-path={surge_url}, update-interval=86400, "
            f"no-alert=0, hidden=1, include-all-proxies=0"
        )

    surge_names = ""
    if surge_providers:
        raw_names = [ln.split("=", 1)[0].strip() for ln in surge_providers.splitlines()]
        surge_names = f", {','.join(raw_names)}"

    stash_names = ", ".join(_provider_names(clash_providers)) if clash_providers else ""

    result = {
        "CLASH_PROXY_PROVIDERS": clash_providers,
        "SURGE_PROXY_PROVIDERS": surge_providers,
        "SURGE_PROVIDER_NAMES": surge_names,
        "STASH_PROVIDER_NAMES": stash_names,
    }
    for key, value in result.items():
        os.environ[key] = value
    return result
