"""Final exec into supervisord (entrypoint.sh:main_init tail equivalent)."""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = "/etc/supervisord.conf"


def build_supervisord_argv(
    extras: Sequence[str] | None,
    *,
    config: str = _DEFAULT_CONFIG,
) -> list[str]:
    """Translate the ``CMD`` tail into the final supervisord argv.

    Bash equivalent::

        if [ "${1#-}" = 'supervisord' ] && [ "$(id -u)" = '0' ]; then
            main_init
            set -- "$@" -n -c /etc/supervisord.conf
        fi
        exec "$@"

    - When the first positional is ``supervisord`` (or missing), emit the
      full ``supervisord -n -c …`` argv.
    - Otherwise, forward the caller's argv verbatim (useful for tests /
      interactive debugging: ``docker run … bash``).
    """
    extras = list(extras or [])
    first = extras[0].lstrip("-") if extras else ""
    if not extras or first == "supervisord":
        return ["supervisord", "-n", "-c", config]
    return extras


def exec_supervisord(extras: Sequence[str] | None = None) -> None:
    """``os.execvp`` into supervisord (never returns on success)."""
    argv = build_supervisord_argv(extras)
    logger.info("移交 Supervisord 接管: %s", " ".join(argv))
    os.execvp(argv[0], argv)
