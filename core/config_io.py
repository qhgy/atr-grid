"""Configuration IO helpers for the active monitoring workflow."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from core.paths import resolve_project_path

DEFAULT_MONITOR_SETTINGS = {
    "桌面通知": True,
    "交互式提醒": False,
    "状态文件路径": "monitor_state.json",
    "提醒冷却秒": 60,
    "连续失败告警阈值": 3,
    "额外休市日期": [],
    "额外交易日期": [],
}


def resolve_config_path(config_path: str | Path = "监控配置.json") -> Path:
    """Resolve the config path from the project root unless already absolute."""
    return resolve_project_path(config_path)


def resolve_log_path(log_path: str | Path) -> Path:
    """Resolve the configured log path relative to the project root."""
    return resolve_project_path(log_path)


def resolve_state_path(state_path: str | Path) -> Path:
    """Resolve the persisted monitor state path relative to the project root."""
    return resolve_project_path(state_path)


def load_monitor_config(config_path: str | Path = "监控配置.json") -> dict[str, Any]:
    """Load and minimally validate the monitoring config."""
    path = resolve_config_path(config_path)
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    apply_monitor_defaults(config)
    validate_monitor_config(config, path)
    return config


def apply_monitor_defaults(config: dict[str, Any]) -> dict[str, Any]:
    """Fill optional settings with stable defaults."""
    settings = config.setdefault("监控设置", {})
    for key, value in DEFAULT_MONITOR_SETTINGS.items():
        settings.setdefault(key, value.copy() if isinstance(value, list) else value)
    return config


def validate_monitor_config(config: dict[str, Any], source: str | Path = "监控配置.json") -> None:
    """Validate only the fields that the active scripts depend on."""
    if "监控列表" not in config or not isinstance(config["监控列表"], list):
        raise ValueError(f"{source} 缺少有效的 监控列表")
    if "监控设置" not in config or not isinstance(config["监控设置"], dict):
        raise ValueError(f"{source} 缺少有效的 监控设置")

    required_settings = ("刷新间隔秒", "价格容差", "日志路径", "声音提醒")
    settings = config["监控设置"]
    for key in required_settings:
        if key not in settings:
            raise ValueError(f"{source} 缺少监控设置字段: {key}")
    for key in ("额外休市日期", "额外交易日期"):
        if key in settings and not isinstance(settings[key], list):
            raise ValueError(f"{source} 监控设置字段 {key} 必须是数组")

    for index, stock in enumerate(config["监控列表"], start=1):
        if not isinstance(stock, dict):
            raise ValueError(f"{source} 第 {index} 条监控项不是对象")
        for key in ("symbol", "name", "targets"):
            if key not in stock:
                raise ValueError(f"{source} 第 {index} 条监控项缺少字段: {key}")
        targets = stock["targets"]
        if not isinstance(targets, dict) or "sell" not in targets or "buy" not in targets:
            raise ValueError(f"{source} 第 {index} 条监控项 targets 不完整")


def backup_config_file(config_path: str | Path = "监控配置.json") -> Path:
    """Create a timestamped backup next to the original config file."""
    path = resolve_config_path(config_path)
    backup_name = f"{path.stem}_备份_{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}"
    backup_path = path.with_name(backup_name)
    shutil.copy2(path, backup_path)
    return backup_path


def save_monitor_config(
    config: dict[str, Any],
    config_path: str | Path = "监控配置.json",
    *,
    create_backup: bool = True,
) -> Path | None:
    """Persist the monitoring config and optionally back up the previous file."""
    path = resolve_config_path(config_path)
    validate_monitor_config(config, path)
    backup_path = backup_config_file(path) if create_backup and path.exists() else None
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
    return backup_path


def load_monitor_state(state_path: str | Path) -> dict[str, Any]:
    """Load persisted monitor state if it exists."""
    path = resolve_state_path(state_path)
    if not path.exists():
        return {"last_alert": {}, "disabled_alerts": [], "active_alerts": []}
    with path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    return {
        "last_alert": state.get("last_alert", {}),
        "disabled_alerts": state.get("disabled_alerts", []),
        "active_alerts": state.get("active_alerts", []),
    }


def save_monitor_state(state: dict[str, Any], state_path: str | Path) -> Path:
    """Persist monitor state for restart-safe alert behavior."""
    path = resolve_state_path(state_path)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    return path
