"""Project path helpers for cross-platform scripts."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT_ENV_VARS = ("AAA_PROJECT_ROOT", "A_STOCK_PROJECT_ROOT")
PYSNOWBALL_ENV_VAR = "PYSNOWBALL_PATH"


def get_project_root() -> Path:
    """Return the repository root, allowing environment overrides."""
    for env_name in PROJECT_ROOT_ENV_VARS:
        env_value = os.environ.get(env_name)
        if env_value:
            return Path(env_value).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def project_path(*parts: os.PathLike[str] | str) -> Path:
    """Build a path anchored at the project root."""
    return get_project_root().joinpath(*parts)


def resolve_project_path(path: os.PathLike[str] | str) -> Path:
    """Resolve absolute paths directly and relative paths from project root."""
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return project_path(candidate)


def get_pysnowball_path() -> Path:
    """Locate the pysnowball package directory."""
    env_value = os.environ.get(PYSNOWBALL_ENV_VAR)
    if env_value:
        return Path(env_value).expanduser().resolve()
    return project_path("pysnowball")


def ensure_pysnowball_path() -> Path:
    """Add pysnowball to sys.path once and return the resolved path."""
    pysnowball_path = get_pysnowball_path()
    path_str = str(pysnowball_path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
    return pysnowball_path
