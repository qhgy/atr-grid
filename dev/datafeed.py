"""数据层：包装 core.market_data，统一前复权口径 + 本地快照缓存。

口径说明：
- 雪球主链路 pysnowball.kline 的 URL 自带 type=before（前复权），无需处理；
- akshare fallback 默认不复权，这里通过 get_kline_data 的注入点强制 adjust="qfq"；
- 每次在线拉取成功后把规整行落到 dev/cache/{symbol}.json（含抓取时间与来源），
  回测可用 offline=True 完全离线复现。
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from core.market_data import get_current_price, get_kline_data

from .config import DEFAULT_CONFIG, StrategyConfig

logger = logging.getLogger(__name__)

_SHANGHAI_TZ = timezone(timedelta(hours=8))
CACHE_DIR = Path(__file__).resolve().parent / "cache"

_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


class DataFeedError(RuntimeError):
    """数据不可用或校验失败。"""


def _akshare_qfq_fetcher(symbol: str, start_date: str, end_date: str):
    """akshare ETF 历史，强制前复权，与雪球主链路口径一致。"""
    import akshare as ak

    return ak.fund_etf_hist_em(
        symbol=symbol,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
    )


def _cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol}.json"


def _save_cache(symbol: str, rows: list[dict[str, Any]], source: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": symbol,
        "source": source,
        "fetched_at": datetime.now(tz=_SHANGHAI_TZ).isoformat(),
        "rows": rows,
    }
    _cache_path(symbol).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _load_cache(symbol: str) -> tuple[list[dict[str, Any]], str] | None:
    path = _cache_path(symbol)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["rows"], f"dev_cache({payload.get('fetched_at', '?')[:10]})"


def _rows_to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True).dt.tz_convert(
        "Asia/Shanghai"
    ).dt.normalize().dt.tz_localize(None)
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame.get(col), errors="coerce")
    frame = frame[_COLUMNS].sort_values("date")
    frame = frame.drop_duplicates(subset="date", keep="last").reset_index(drop=True)
    return frame


def _validate(symbol: str, frame: pd.DataFrame, min_rows: int) -> list[str]:
    warnings: list[str] = []
    if len(frame) < min_rows:
        raise DataFeedError(f"{symbol} 仅 {len(frame)} 根日线，少于要求的 {min_rows} 根")
    ohlc = frame[["open", "high", "low", "close"]]
    nan_rows = int(ohlc.isna().any(axis=1).sum())
    if nan_rows:
        warnings.append(f"{symbol}: {nan_rows} 行 OHLC 含缺失值，已剔除")
    bad = (frame["high"] < frame["low"]).sum()
    if bad:
        raise DataFeedError(f"{symbol} 存在 {int(bad)} 行 high < low，数据异常")
    # 单日跳空超过 ±12% 提示（前复权序列正常不应出现，若出现多半是复权口径混用）
    gap = (frame["close"].pct_change().abs() > 0.12).sum()
    if gap:
        warnings.append(f"{symbol}: {int(gap)} 个交易日 |涨跌| > 12%，注意复权口径")
    return warnings


def load_history(
    symbol: str,
    *,
    cfg: StrategyConfig = DEFAULT_CONFIG,
    count: int | None = None,
    offline: bool = False,
    min_rows: int = 60,
) -> tuple[pd.DataFrame, str, list[str]]:
    """加载单标的日线，返回 (frame, source, warnings)。"""
    count = count or cfg.kline_count
    rows: list[dict[str, Any]] | None = None
    source = "missing"

    if not offline:
        rows, source = get_kline_data(
            symbol, count=count, akshare_fetcher=_akshare_qfq_fetcher
        )
        if rows:
            _save_cache(symbol, _jsonable(rows), source)
        else:
            logger.warning("%s 在线获取失败，尝试 dev/cache 离线快照", symbol)

    if not rows:
        cached = _load_cache(symbol)
        if cached is None:
            raise DataFeedError(f"{symbol} 在线与 dev/cache 均无数据")
        rows, source = cached

    frame = _rows_to_frame(rows)
    frame = frame.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    warnings = _validate(symbol, frame, min_rows)
    if offline or source.startswith("dev_cache"):
        warnings.append(f"{symbol}: 使用离线快照（{source}），非最新行情")
    return frame, source, warnings


def _jsonable(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = ("timestamp", "open", "high", "low", "close", "volume")
    out = []
    for row in rows:
        item = {k: row.get(k) for k in keep}
        out.append({k: (None if _is_nan(v) else v) for k, v in item.items()})
    return out


def _is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def load_bundle(
    cfg: StrategyConfig = DEFAULT_CONFIG, *, offline: bool = False
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """加载主标的 + 过滤指数。指数失败不阻塞主流程（过滤器降级为中性）。"""
    bundle: dict[str, pd.DataFrame] = {}
    warnings: list[str] = []

    frame, source, warns = load_history(symbol=cfg.symbol, cfg=cfg, offline=offline)
    bundle[cfg.symbol] = frame
    warnings.extend(warns)
    warnings.append(f"{cfg.symbol}: 数据来源 {source}，{len(frame)} 根日线")

    for index_symbol in cfg.index_symbols:
        try:
            iframe, isource, iwarns = load_history(
                symbol=index_symbol, cfg=cfg, offline=offline
            )
            bundle[index_symbol] = iframe
            warnings.extend(iwarns)
        except DataFeedError as exc:
            warnings.append(f"{index_symbol}: 获取失败（{exc}），指数过滤降级为中性")
    return bundle, warnings


def latest_price(symbol: str) -> float | None:
    """实时价（仅每日信号用，回测不依赖）。"""
    return get_current_price(symbol)
