"""Boot-time stages ported from ``entrypoint.sh`` §15 / §16.

Each module exposes a single, idempotent entry point that matches its Bash
counterpart 1:1 so the Python orchestration in ``entrypoint.py`` can retire
``entrypoint.sh`` entirely.
"""
