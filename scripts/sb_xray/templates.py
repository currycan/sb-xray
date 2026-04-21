"""Template rendering (entrypoint.sh §5 `_apply_tpl` equivalent).

Uses Jinja2 with ``StrictUndefined`` so a missing variable fails loudly
(the Bash ``envsubst`` silently turned missing vars into empty strings,
which was a frequent source of broken configs). The templates in this
repo use shell-style ``${VAR}`` placeholders, so we pre-convert them
to Jinja2's ``{{ VAR }}`` before rendering.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

import jinja2

_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class TemplateError(RuntimeError):
    """Raised when a template reference is undefined or output is invalid."""


def _shell_to_jinja(text: str) -> str:
    """Convert ``${VAR}`` → ``{{ VAR }}``. Leaves other text untouched."""
    return _PLACEHOLDER_RE.sub(r"{{ \1 }}", text)


def _build_env() -> jinja2.Environment:
    return jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )


def _merged_context(context: Mapping[str, object] | None) -> dict[str, object]:
    merged: dict[str, object] = dict(os.environ)
    if context:
        merged.update(context)
    return merged


def render_string(source: str, *, context: Mapping[str, object] | None = None) -> str:
    """Render a template string. Context falls back to ``os.environ``."""
    try:
        template = _build_env().from_string(_shell_to_jinja(source))
        return template.render(_merged_context(context))
    except jinja2.UndefinedError as exc:
        raise TemplateError(f"undefined variable: {exc}") from exc
    except jinja2.TemplateSyntaxError as exc:
        raise TemplateError(f"template syntax error: {exc}") from exc


def render_file(
    src: Path | str,
    dest: Path | str,
    *,
    context: Mapping[str, object] | None = None,
) -> None:
    """Render ``src`` to ``dest``.

    If ``dest`` ends in ``.json``, the rendered output is validated
    (and reformatted with ``json.dumps``).
    """
    src_path = Path(src)
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    rendered = render_string(src_path.read_text(encoding="utf-8"), context=context)

    if dest_path.suffix == ".json":
        try:
            data = json.loads(rendered)
        except json.JSONDecodeError as exc:
            raise TemplateError(f"invalid JSON after rendering {src_path}: {exc}") from exc
        dest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    else:
        dest_path.write_text(rendered, encoding="utf-8")
