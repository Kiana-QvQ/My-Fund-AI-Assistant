"""Query canonical A-share PE-TTM and near-10y percentile for the project.

Used by both local checks and the scheduled market snapshot job so README /
email / build_plan share one data path.

Each call hits live AKShare (CSIndex + Legu history) and recomputes the rolling
percentile from the full PE series — `data/market_snapshot.json` is only the
persisted result of that live calculation, never a substitute data source for
trading decisions.
"""

from __future__ import annotations

import akshare as ak
import pandas as pd


INDEXES = (
    ("沪深300", "000300"),
    ("中证500", "000905"),
)
WINDOW_YEARS = 10
CSINDEX_REQUIRED_COLUMNS = ("日期",)
HISTORY_REQUIRED_COLUMNS = ("日期", "滚动市盈率")


def _to_float(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def assert_akshare_csindex_contract(frame: pd.DataFrame, symbol: str) -> None:
    if frame is None or frame.empty:
        raise RuntimeError(f"{symbol} AKShare CSIndex 返回空表，疑似接口变更/失败")
    missing = [col for col in CSINDEX_REQUIRED_COLUMNS if col not in frame.columns]
    if missing:
        raise RuntimeError(
            f"{symbol} AKShare CSIndex 字段变更，缺少 {missing}；"
            f"实际列={list(frame.columns)}"
        )


def assert_akshare_pe_history_contract(frame: pd.DataFrame, symbol: str) -> None:
    if frame is None or frame.empty:
        raise RuntimeError(f"{symbol} AKShare 滚动市盈率历史为空，疑似接口变更/失败")
    missing = [col for col in HISTORY_REQUIRED_COLUMNS if col not in frame.columns]
    if missing:
        raise RuntimeError(
            f"{symbol} AKShare 历史PE字段变更，缺少 {missing}；"
            f"实际列={list(frame.columns)}"
        )


def query_pe_snapshot(window_years: int = WINDOW_YEARS) -> dict[str, dict]:
    result = {}
    for symbol, code in INDEXES:
        current_frame = ak.stock_zh_index_value_csindex(symbol=code)
        assert_akshare_csindex_contract(current_frame, symbol)
        current = current_frame.iloc[0]
        history = ak.stock_index_pe_lg(symbol=symbol).copy()
        assert_akshare_pe_history_contract(history, symbol)
        history["日期"] = pd.to_datetime(history["日期"])
        history = history.sort_values("日期")
        history["滚动市盈率"] = pd.to_numeric(
            history["滚动市盈率"], errors="coerce"
        )
        history = history.dropna(subset=["滚动市盈率"])
        if history.empty:
            raise RuntimeError(f"{symbol} 滚动市盈率历史为空")

        end = history["日期"].iloc[-1]
        start = end - pd.DateOffset(years=window_years)
        window = history[history["日期"] >= start]
        pe_series = window["滚动市盈率"]
        current_pe = float(pe_series.iloc[-1])
        percentile = float((pe_series <= current_pe).mean() * 100)

        start_1y = end - pd.DateOffset(years=1)
        window_1y = history[history["日期"] >= start_1y]
        pe_1y = window_1y["滚动市盈率"]
        percentile_1y = (
            float((pe_1y <= current_pe).mean() * 100) if not pe_1y.empty else None
        )

        result[symbol] = {
            "index_code": code,
            "date": str(current["日期"]),
            "pe_ttm": round(current_pe, 2),
            "pe_percentile": round(percentile, 2),
            "pe_percentile_1y": round(percentile_1y, 2)
            if percentile_1y is not None
            else None,
            "verified": True,
            "tradeable": True,
            "window": f"近{window_years}年滚动PE分位",
            "window_1y": "近1年滚动PE分位（启动仓）",
            "window_start": str(window["日期"].iloc[0].date()),
            "window_end": str(window["日期"].iloc[-1].date()),
            "csindex_pe_1": _to_float(current.get("市盈率1")),
            "csindex_pe_2": _to_float(current.get("市盈率2")),
            "history_start": str(history["日期"].iloc[0].date()),
            "history_count": int(len(pe_series)),
            "history_count_1y": int(len(pe_1y)),
            "source": "AKShare CSIndex + Legu（近10年滚动市盈率分位）",
        }
    return result


if __name__ == "__main__":
    for name, item in query_pe_snapshot().items():
        print(
            f"{name}: PE={item['pe_ttm']:.2f}, "
            f"percentile={item['pe_percentile']:.2f}%, "
            f"window={item['window_start']}~{item['window_end']}, "
            f"count={item['history_count']}"
        )
        print(
            f"  csindex date={item['date']}, "
            f"pe1={item['csindex_pe_1']}, pe2={item['csindex_pe_2']}"
        )
