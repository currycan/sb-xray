"""Environment-variable management (entrypoint.sh §6 equivalent).

Persistence format matches the existing `${ENV_FILE}`:

    export KEY='VALUE'
    export OTHER='another value'

Each `ensure_var` call follows the Bash priority ladder:
  1. Already in ``os.environ`` (docker-compose or caller set it) → keep.
  2. Present in the persist file as ``export KEY='…'`` → load.
  3. Fall back to ``generator()`` or ``default=``.

When branch 3 fires and ``persist=True``, the new value is appended
(with any previous line for the same key removed first).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Final

_EXPORT_RE_TMPL: Final[str] = r"^export {key}='(.*)'$"


class EnvManager:
    """Read/write the persisted sb-xray environment file."""

    def __init__(self, env_file: Path | str) -> None:
        self.path = Path(env_file)
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch()

    # -- read --------------------------------------------------------

    def get(self, key: str) -> str | None:
        """Return the value stored under ``key`` in the env file, or None."""
        pattern = re.compile(_EXPORT_RE_TMPL.format(key=re.escape(key)), re.MULTILINE)
        matches = pattern.findall(self.path.read_text(encoding="utf-8"))
        return matches[-1] if matches else None

    # -- write -------------------------------------------------------

    def _persist(self, key: str, value: str) -> None:
        """Remove any existing ``export key=…`` line and append the new one."""
        pattern = re.compile(_EXPORT_RE_TMPL.format(key=re.escape(key)), re.MULTILINE)
        existing = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        stripped = pattern.sub("", existing)
        cleaned = "\n".join(line for line in stripped.splitlines() if line.strip())
        if cleaned:
            cleaned += "\n"
        cleaned += f"export {key}='{value}'\n"
        self.path.write_text(cleaned, encoding="utf-8")

    # -- ensure_var --------------------------------------------------

    def ensure_var(
        self,
        key: str,
        *,
        default: str | None = None,
        generator: Callable[[], str] | None = None,
        persist: bool = True,
    ) -> str:
        """Three-tier lookup: shell env → file → generator/default."""
        existing = os.environ.get(key)
        if existing:
            return existing

        persisted = self.get(key)
        if persisted is not None:
            os.environ[key] = persisted
            return persisted

        if generator is not None:
            value = generator()
        elif default is not None:
            value = default
        else:
            raise KeyError(
                f"{key!r} not in environment, not in {self.path}, and no generator/default provided"
            )

        os.environ[key] = value
        if persist:
            self._persist(key, value)
        return value

    # -- ensure_key_pair ---------------------------------------------

    def ensure_key_pair(
        self,
        name: str,
        key1: str,
        key2: str,
        *,
        generator: Callable[[], dict[str, str]],
    ) -> dict[str, str]:
        """Atomic two-key generator. Either both are loaded from file, or
        both are freshly generated and persisted together."""
        v1, v2 = self.get(key1), self.get(key2)
        if v1 is not None and v2 is not None:
            os.environ[key1] = v1
            os.environ[key2] = v2
            return {key1: v1, key2: v2}

        produced = generator()
        missing = {key1, key2} - produced.keys()
        if missing:
            raise RuntimeError(f"{name} generator did not return required keys: {sorted(missing)}")
        os.environ[key1] = produced[key1]
        os.environ[key2] = produced[key2]
        self._persist(key1, produced[key1])
        self._persist(key2, produced[key2])
        return produced

    # -- check_required ---------------------------------------------

    def check_required(self, *names: str) -> None:
        """Raise if any of the given environment variables is unset/empty."""
        missing = [n for n in names if not os.environ.get(n)]
        if missing:
            raise RuntimeError(f"required environment variables missing: {', '.join(missing)}")
