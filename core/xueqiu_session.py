"""Helpers for loading Xueqiu cookies into the current process environment."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from core.paths import project_path, resolve_project_path

XUEQIU_TOKEN_ENV_VAR = "XUEQIUTOKEN"
XUEQIU_COOKIE_FILE_ENV_VAR = "XUEQIU_COOKIE_FILE"
DEFAULT_COOKIE_FILES = ("xq_token.txt", "xueqiu.com_cookies.txt")
_KNOWN_COOKIE_PATHS = (
    r"D:\000000znb\1080\x1080x_attachments\xueqiu.com_cookies.txt",
)

_EXPIRY_WARN_DAYS = 30   # 到期前 30 天开始提醒


def ensure_xueqiu_token_loaded(*, base_dir: str | Path | None = None) -> str | None:
    """Load the Xueqiu cookie string into ``XUEQIUTOKEN`` if not already set."""
    existing = os.environ.get(XUEQIU_TOKEN_ENV_VAR)
    if existing:
        return existing

    candidate_path = _resolve_cookie_path(base_dir=base_dir)
    _warn_if_expiring_soon(candidate_path)

    cookie_text = _load_cookie_from_path(candidate_path)
    if not cookie_text:
        return None

    os.environ[XUEQIU_TOKEN_ENV_VAR] = cookie_text
    return cookie_text


def load_xueqiu_cookie_text(*, base_dir: str | Path | None = None) -> str | None:
    """Load and normalize a Xueqiu cookie file if present."""
    candidate_path = _resolve_cookie_path(base_dir=base_dir)
    return _load_cookie_from_path(candidate_path)


def _load_cookie_from_path(candidate_path: Path | None) -> str | None:
    if not candidate_path or not candidate_path.exists():
        return None
    raw_text = candidate_path.read_text(encoding="utf-8").strip()
    if not raw_text:
        return None
    return parse_cookie_text(raw_text)


def parse_cookie_text(raw_text: str) -> str:
    """Normalize either a raw cookie header or a Netscape cookie file."""
    if "# Netscape HTTP Cookie File" not in raw_text:
        return raw_text.strip()

    pairs: list[str] = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _, _, _, _, name, value = parts[:7]
        if "xueqiu.com" not in domain:
            continue
        pairs.append(f"{name}={value}")
    return "; ".join(pairs).strip()


def _warn_if_expiring_soon(cookie_path: Path | None) -> None:
    """Print a warning to stderr if the Netscape cookie file expires within _EXPIRY_WARN_DAYS."""
    if not cookie_path or not cookie_path.exists():
        return
    raw_text = cookie_path.read_text(encoding="utf-8")
    if "# Netscape HTTP Cookie File" not in raw_text:
        return  # raw token file — no expiry info

    earliest_expiry: int | None = None
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain = parts[0]
        if "xueqiu.com" not in domain:
            continue
        try:
            expiry_ts = int(parts[4])
        except (ValueError, IndexError):
            continue
        if expiry_ts > 0:
            if earliest_expiry is None or expiry_ts < earliest_expiry:
                earliest_expiry = expiry_ts

    if earliest_expiry is None:
        return

    now_ts = int(datetime.now(timezone.utc).timestamp())
    days_left = (earliest_expiry - now_ts) // 86400
    expiry_str = datetime.fromtimestamp(earliest_expiry, tz=timezone.utc).strftime("%Y-%m-%d")

    if days_left < 0:
        print(
            f"[xueqiu_session] ⚠️  Cookie 已于 {expiry_str} 过期！请重新导出。",
            file=sys.stderr,
        )
    elif days_left <= _EXPIRY_WARN_DAYS:
        print(
            f"[xueqiu_session] ⚠️  Cookie 将于 {expiry_str} 到期（还剩 {days_left} 天），请及时更换。",
            file=sys.stderr,
        )


def _resolve_cookie_path(*, base_dir: str | Path | None = None) -> Path | None:
    env_path = os.environ.get(XUEQIU_COOKIE_FILE_ENV_VAR)
    if env_path:
        return resolve_project_path(env_path)

    root = resolve_project_path(base_dir) if base_dir else project_path()
    for filename in DEFAULT_COOKIE_FILES:
        candidate = root / filename
        if candidate.exists():
            return candidate

    for known in _KNOWN_COOKIE_PATHS:
        candidate = Path(known)
        if candidate.exists():
            return candidate
    return None
