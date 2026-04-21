"""Random value generation (entrypoint.sh §4 equivalent).

Replaces shell-level `tr -dc ... < /dev/urandom | head -c N` with
`secrets.choice` + Python's cryptographically secure RNG.
"""

from __future__ import annotations

import secrets
import string
import uuid as _uuid
from typing import Final, Literal

Kind = Literal["port", "uuid", "password", "path", "hex"]

_PORT_MIN: Final[int] = 32000
_PORT_MAX: Final[int] = 38000  # matches Bash $(( RANDOM % 6001 + 32000 ))
_PASSWORD_ALPHABET: Final[str] = string.ascii_letters + string.digits
_PATH_ALPHABET: Final[str] = string.ascii_lowercase + string.digits


def generate(kind: Kind, length: int = 12) -> str:
    """Produce a random value of the requested shape.

    - ``port``     : numeric string in [32000, 38000] (inclusive)
    - ``uuid``     : UUIDv4 (``xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx``)
    - ``password`` : ``length`` chars from ``[A-Za-z0-9]``
    - ``path``     : ``length`` chars from ``[a-z0-9]``
    - ``hex``      : ``2 * length`` hex chars (``secrets.token_hex``)
    """
    if kind == "port":
        return str(_PORT_MIN + secrets.randbelow(_PORT_MAX - _PORT_MIN + 1))
    if kind == "uuid":
        return str(_uuid.uuid4())
    if kind == "password":
        return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))
    if kind == "path":
        return "".join(secrets.choice(_PATH_ALPHABET) for _ in range(length))
    if kind == "hex":
        return secrets.token_hex(length)
    raise ValueError(f"unknown kind: {kind!r}")
