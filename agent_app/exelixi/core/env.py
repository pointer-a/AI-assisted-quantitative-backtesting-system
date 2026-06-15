from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

_VAR_RE = re.compile(r"\$([A-Z_][A-Z0-9_]*|\{[A-Z_][A-Z0-9_]*\})")


def load_env() -> None:
    """Load the configured .env file, then expand ``$VARIABLE`` references manually.

    Works with any version of python-dotenv (avoids the ``expand_vars`` kwarg
    that was added in 0.21.0).
    """
    explicit = env_var("EXELIXI_ENV_FILE")
    if explicit:
        load_dotenv(dotenv_path=Path(explicit).expanduser())
    else:
        load_dotenv()
    _expand_env_vars()


def env_var(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is not None:
        return value
    if name.startswith("EXELIXI_"):
        legacy = "MOKIO_" + name.removeprefix("EXELIXI_")
        value = os.getenv(legacy)
        if value is not None:
            return value
    return default


def _expand_env_vars() -> None:
    """Replace ``$VAR`` / ``${VAR}`` patterns in ``os.environ`` entries
    with the value of the referenced environment variable.
    """
    for key in list(os.environ):
        val = os.environ[key]
        if "$" not in val:
            continue
        expanded = _VAR_RE.sub(_replace_var, val)
        if expanded != val:
            os.environ[key] = expanded


def _replace_var(m: re.Match) -> str:
    name = m.group(1).strip("{}")
    return os.environ.get(name, m.group(0))
