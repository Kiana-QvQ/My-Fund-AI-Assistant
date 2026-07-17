"""52-week high drawdown for equity indexes (price filter for starter buys).

Index level alone never triggers buys — it only gates bootstrap together with
1y PE percentile. Fail-closed: missing drawdown blocks starter positions.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Callable

import pandas as pd

CST = timezone(timedelta(hours=8))

# Trading-day approx for 52 weeks.
LOOKBACK_TRADING_DAYS = 252

A_SHARE_SYMBOLS = {
    "沪深300": "sh000300",
    "中证500": "sh000905",
}


def _today() -> date:
    return datetime.now(CST).date()


def compute_drawdown_from_closes(
    closes: list[float],
    *,
    lookback: int = LOOKBACK_TRADING_DAYS,
) -> dict:
    """Return drawdown vs max close in the trailing window.

    drawdown_from_52w_high is a fraction in [0, 1], e.g. 0.12 = 12% below high.
    """
    series = [float(x) for x in closes if isinstance(x, (int, float)) and x > 0]
    if len(series) < 20:
        raise RuntimeError(f"收盘价样本不足（{len(series)}），无法计算52周回撤")
    window = series[-lookback:] if len(series) >= lookback else series
    high = max(window)
    last = window[-1]
    if high <= 0:
        raise RuntimeError("52周高点非法")
    drawdown = (high - last) / high
    return {
        "close": round(last, 4),
        "high_52w": round(high, 4),
        "drawdown_from_52w_high": round(drawdown, 4),
        "drawdown_from_52w_high_pct": round(drawdown * 100, 2),
        "lookback_points": len(window),
    }


def _fetch_a_share_closes(symbol: str) -> list[float]:
    import akshare as ak

    frame = ak.stock_zh_index_daily(symbol=symbol)
    if frame is None or getattr(frame, "empty", True):
        raise RuntimeError(f"AKShare {symbol} 日线为空")
    if "close" not in frame.columns:
        raise RuntimeError(
            f"AKShare {symbol} 日线字段变更，缺少 close；实际列={list(frame.columns)}"
        )
    closes = pd.to_numeric(frame["close"], errors="coerce").dropna().tolist()
    return [float(x) for x in closes]


def _fetch_spx_closes() -> tuple[list[float], str]:
    errors: list[str] = []

    try:
        import akshare as ak

        frame = ak.index_global_hist_em(symbol="标普500")
        if frame is not None and not frame.empty:
            # Column names vary by locale; prefer 最新价 / close.
            close_col = None
            for candidate in ("最新价", "收盘", "close", "Close"):
                if candidate in frame.columns:
                    close_col = candidate
                    break
            if close_col is None and len(frame.columns) >= 5:
                # Typical EM layout: 日期, 开盘, 收盘, 最高, 最低, ...
                close_col = frame.columns[2]
            if close_col is None:
                raise RuntimeError(f"标普500全局指数缺少收盘列：{list(frame.columns)}")
            closes = pd.to_numeric(frame[close_col], errors="coerce").dropna().tolist()
            if len(closes) >= 20:
                return [float(x) for x in closes], "akshare:index_global_hist_em:标普500"
            raise RuntimeError(f"标普500收盘样本不足（{len(closes)}）")
    except Exception as exc:
        errors.append(f"akshare: {exc}")

    try:
        import yfinance as yf

        hist = yf.Ticker("^GSPC").history(period="2y")
        if hist is None or hist.empty or "Close" not in hist.columns:
            raise RuntimeError("yfinance ^GSPC 无 Close")
        closes = [float(x) for x in hist["Close"].tolist() if float(x) > 0]
        if len(closes) < 20:
            raise RuntimeError(f"yfinance ^GSPC 样本不足（{len(closes)}）")
        return closes, "yfinance:^GSPC"
    except Exception as exc:
        errors.append(f"yfinance: {exc}")

    raise RuntimeError("；".join(errors))


def fetch_index_drawdown(name: str) -> dict:
    """Fetch live drawdown metrics for one index. Raises on failure."""
    as_of = _today().isoformat()
    if name in A_SHARE_SYMBOLS:
        symbol = A_SHARE_SYMBOLS[name]
        closes = _fetch_a_share_closes(symbol)
        metrics = compute_drawdown_from_closes(closes)
        metrics.update(
            {
                "index": name,
                "as_of": as_of,
                "source": f"akshare:stock_zh_index_daily:{symbol}",
                "status": "ok",
            }
        )
        return metrics

    if name == "标普500":
        closes, source = _fetch_spx_closes()
        metrics = compute_drawdown_from_closes(closes)
        metrics.update(
            {
                "index": name,
                "as_of": as_of,
                "source": source,
                "status": "ok",
            }
        )
        return metrics

    if name == "纳斯达克100":
        return {
            "index": name,
            "as_of": as_of,
            "drawdown_from_52w_high": None,
            "drawdown_from_52w_high_pct": None,
            "status": "skipped",
            "reason": "纳指仅参考，不参与启动仓回撤过滤",
            "source": None,
        }

    raise RuntimeError(f"未配置回撤数据源: {name}")


def attach_drawdowns(
    indexes: dict[str, dict],
    *,
    names: tuple[str, ...] = ("沪深300", "中证500", "标普500"),
    fetcher: Callable[[str], dict] | None = None,
) -> dict[str, dict]:
    """Merge drawdown fields into index snapshot dict (mutate + return)."""
    fetch = fetcher or fetch_index_drawdown
    for name in names:
        item = indexes.setdefault(name, {})
        try:
            metrics = fetch(name)
            item["drawdown_from_52w_high"] = metrics.get("drawdown_from_52w_high")
            item["drawdown_from_52w_high_pct"] = metrics.get(
                "drawdown_from_52w_high_pct"
            )
            item["price_close"] = metrics.get("close")
            item["price_high_52w"] = metrics.get("high_52w")
            item["drawdown_source"] = metrics.get("source")
            item["drawdown_status"] = metrics.get("status", "ok")
            if metrics.get("reason"):
                item["drawdown_reason"] = metrics["reason"]
        except Exception as exc:
            item["drawdown_from_52w_high"] = None
            item["drawdown_from_52w_high_pct"] = None
            item["drawdown_status"] = "fetch_failed"
            item["drawdown_reason"] = str(exc)
    # Explicit skip for NDX
    ndx = indexes.setdefault("纳斯达克100", {})
    if "drawdown_status" not in ndx:
        ndx["drawdown_from_52w_high"] = None
        ndx["drawdown_from_52w_high_pct"] = None
        ndx["drawdown_status"] = "skipped"
        ndx["drawdown_reason"] = "纳指仅参考，不参与启动仓回撤过滤"
    return indexes
