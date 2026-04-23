"""Shared monitoring logic for Windows and Linux frontends."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Callable

from core.market_data import get_current_price

NowProvider = Callable[[], datetime]


def is_trading_day(
    day: date | datetime,
    extra_closed_dates: set[date] | None = None,
    extra_open_dates: set[date] | None = None,
) -> bool:
    """Return whether the day should be treated as a trading day."""
    current_day = day.date() if isinstance(day, datetime) else day
    extra_closed_dates = extra_closed_dates or set()
    extra_open_dates = extra_open_dates or set()
    if current_day in extra_open_dates:
        return True
    if current_day in extra_closed_dates:
        return False
    return current_day.weekday() < 5


def is_trading_time(
    now: datetime | None = None,
    *,
    extra_closed_dates: set[date] | None = None,
    extra_open_dates: set[date] | None = None,
) -> bool:
    """Return whether the timestamp is within A-share trading hours."""
    now = now or datetime.now()
    if not is_trading_day(now, extra_closed_dates=extra_closed_dates, extra_open_dates=extra_open_dates):
        return False

    current_time = now.time()
    morning_start = datetime.strptime("09:30", "%H:%M").time()
    morning_end = datetime.strptime("11:30", "%H:%M").time()
    afternoon_start = datetime.strptime("13:00", "%H:%M").time()
    afternoon_end = datetime.strptime("15:00", "%H:%M").time()

    return (morning_start <= current_time <= morning_end) or (
        afternoon_start <= current_time <= afternoon_end
    )


def get_next_trading_time(
    now: datetime | None = None,
    *,
    extra_closed_dates: set[date] | None = None,
    extra_open_dates: set[date] | None = None,
) -> datetime | None:
    """Return the next trading session start time."""
    now = now or datetime.now()
    current_time = now.time()
    morning_start = datetime.strptime("09:30", "%H:%M").time()
    afternoon_start = datetime.strptime("13:00", "%H:%M").time()
    end_of_day = datetime.strptime("15:00", "%H:%M").time()

    if not is_trading_day(now, extra_closed_dates=extra_closed_dates, extra_open_dates=extra_open_dates):
        probe = now + timedelta(days=1)
        while not is_trading_day(probe, extra_closed_dates=extra_closed_dates, extra_open_dates=extra_open_dates):
            probe += timedelta(days=1)
        return probe.replace(hour=9, minute=30, second=0, microsecond=0)

    if current_time < morning_start:
        return now.replace(hour=9, minute=30, second=0, microsecond=0)
    if morning_start < current_time < afternoon_start:
        return now.replace(hour=13, minute=0, second=0, microsecond=0)
    if current_time > end_of_day:
        tomorrow = now + timedelta(days=1)
        while not is_trading_day(tomorrow, extra_closed_dates=extra_closed_dates, extra_open_dates=extra_open_dates):
            tomorrow = tomorrow + timedelta(days=1)
        return tomorrow.replace(hour=9, minute=30, second=0, microsecond=0)
    return None


class MonitorCore:
    """Cross-platform monitoring core without UI side effects."""

    def __init__(self, watch_list, settings, *, quote_provider=None, now_provider: NowProvider | None = None):
        self.watch_list = watch_list
        self.interval = settings["刷新间隔秒"]
        self.tolerance = settings["价格容差"] / 100
        self.cooldown_seconds = settings.get("提醒冷却秒", 60)
        self.failure_alert_threshold = settings.get("连续失败告警阈值", 3)
        self.quote_provider = quote_provider
        self.now_provider = now_provider or datetime.now
        self.extra_closed_dates = _parse_date_set(settings.get("额外休市日期", []))
        self.extra_open_dates = _parse_date_set(settings.get("额外交易日期", []))
        self.price_cache: dict[str, tuple[float, float]] = {}
        self.last_alert: dict[str, float] = {}
        self.last_seen_price: dict[str, float] = {}
        self.disabled_alerts: set[str] = set()
        self.active_alerts: set[str] = set()
        self.failure_counts: dict[str, int] = {}

    def load_state(self, state: dict | None) -> None:
        """Load persisted alert state."""
        state = state or {}
        self.last_alert = {key: float(value) for key, value in state.get("last_alert", {}).items()}
        self.disabled_alerts = set(state.get("disabled_alerts", []))
        self.active_alerts = set(state.get("active_alerts", []))

    def export_state(self) -> dict:
        """Export persisted alert state."""
        return {
            "last_alert": self.last_alert,
            "disabled_alerts": sorted(self.disabled_alerts),
            "active_alerts": sorted(self.active_alerts),
        }

    def disable_alert(self, symbol: str, action: str, target_price: float) -> None:
        """Disable a specific alert for the current process session."""
        alert_key = self._alert_key(symbol, action, target_price)
        self.disabled_alerts.add(alert_key)
        self.active_alerts.add(alert_key)

    def get_real_price(self, symbol: str) -> float | None:
        """Fetch current price with interval-based in-memory cache."""
        now_ts = time.time()
        cached = self.price_cache.get(symbol)
        if cached and now_ts - cached[1] < self.interval:
            return cached[0]

        price = get_current_price(symbol, quote_fetcher=self.quote_provider)
        if price is not None:
            self.price_cache[symbol] = (price, now_ts)
        return price

    def check_target(
        self,
        symbol: str,
        current_price: float,
        target_price: float,
        action: str,
        *,
        previous_price: float | None = None,
    ) -> bool:
        """Check whether a target price has been reached within tolerance or via crossing."""
        alert_key = self._alert_key(symbol, action, target_price)
        if alert_key in self.disabled_alerts:
            return False

        if target_price <= 0:
            return False

        in_trigger_zone = self._in_trigger_zone(current_price, target_price, action)
        if not in_trigger_zone:
            self.active_alerts.discard(alert_key)
            return False

        crossing = self._crossed_target(previous_price, current_price, target_price, action)
        if alert_key in self.active_alerts and not crossing:
            return False

        previous = self.last_alert.get(alert_key)
        if previous and time.time() - previous < self.cooldown_seconds:
            return False

        self.last_alert[alert_key] = time.time()
        self.active_alerts.add(alert_key)
        return True

    def status_text(self, current_price: float, sell_target: float, buy_target: float) -> str:
        """Generate the human-readable distance-to-target status."""
        to_sell = (sell_target - current_price) / current_price * 100
        to_buy = (current_price - buy_target) / current_price * 100
        if to_sell <= 0:
            return f"💎 已超过卖点 {sell_target:.2f} (超{abs(to_sell):.1f}%)"
        if to_buy <= 0:
            return f"📉 已低于买点 {buy_target:.2f} (低{abs(to_buy):.1f}%)"
        return f"📊 距卖点 +{to_sell:.1f}%, 距买点 -{to_buy:.1f}%"

    def monitor_once(self) -> dict:
        """Execute one monitoring cycle and return structured results."""
        now = self.now_provider()
        if not is_trading_time(
            now,
            extra_closed_dates=self.extra_closed_dates,
            extra_open_dates=self.extra_open_dates,
        ):
            return {
                "trading": False,
                "now": now,
                "next_time": get_next_trading_time(
                    now,
                    extra_closed_dates=self.extra_closed_dates,
                    extra_open_dates=self.extra_open_dates,
                ),
                "rows": [],
                "alerts": [],
                "health_alerts": [],
            }

        rows = []
        alerts = []
        health_alerts = []
        for stock in self.watch_list:
            symbol = stock["symbol"]
            name = stock["name"]
            targets = stock["targets"]
            price = self.get_real_price(symbol)
            if price is None:
                self.failure_counts[symbol] = self.failure_counts.get(symbol, 0) + 1
                rows.append({"symbol": symbol, "name": name, "price": None, "status": "获取价格失败"})
                if self.failure_counts[symbol] == self.failure_alert_threshold:
                    health_alerts.append(
                        {
                            "symbol": symbol,
                            "name": name,
                            "message": f"{name}({symbol}) 已连续 {self.failure_counts[symbol]} 次获取价格失败",
                        }
                    )
                continue
            self.failure_counts[symbol] = 0

            sell_target = targets["sell"]
            buy_target = targets["buy"]
            status = self.status_text(price, sell_target, buy_target)
            rows.append({"symbol": symbol, "name": name, "price": price, "status": status, "targets": targets})
            previous_price = self.last_seen_price.get(symbol)

            if self.check_target(symbol, price, sell_target, "sell", previous_price=previous_price):
                alerts.append(
                    {"symbol": symbol, "name": name, "price": price, "target": sell_target, "action": "sell"}
                )
            if self.check_target(symbol, price, buy_target, "buy", previous_price=previous_price):
                alerts.append(
                    {"symbol": symbol, "name": name, "price": price, "target": buy_target, "action": "buy"}
                )
            self.last_seen_price[symbol] = price

        return {"trading": True, "now": now, "rows": rows, "alerts": alerts, "health_alerts": health_alerts}

    @staticmethod
    def _alert_key(symbol: str, action: str, target_price: float) -> str:
        return f"{symbol}_{action}_{target_price}"

    def _in_trigger_zone(self, current_price: float, target_price: float, action: str) -> bool:
        if action == "sell":
            return current_price >= target_price * (1 - self.tolerance)
        return current_price <= target_price * (1 + self.tolerance)

    @staticmethod
    def _crossed_target(
        previous_price: float | None,
        current_price: float,
        target_price: float,
        action: str,
    ) -> bool:
        if previous_price is None:
            return False
        if action == "sell":
            return previous_price < target_price <= current_price
        return previous_price > target_price >= current_price


def _parse_date_set(values: list[str]) -> set[date]:
    parsed: set[date] = set()
    for value in values:
        parsed.add(datetime.strptime(value, "%Y-%m-%d").date())
    return parsed
