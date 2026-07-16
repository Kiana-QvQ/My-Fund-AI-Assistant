"""Query the project's canonical A-share PE and percentile data.

This file started as a local check script. It is now also used by the
scheduled market snapshot job so the displayed values cannot drift between
manual checks and GitHub Actions.
"""

from __future__ import annotations

import akshare as ak
import pandas as pd


INDEXES = (
    ("沪深300", "000300"),
    ("中证500", "000905"),
)


def query_pe_snapshot() -> dict[str, dict]:
    result = {}
    for symbol, code in INDEXES:
        current = ak.stock_zh_index_value_csindex(symbol=code).iloc[0]
        history = ak.stock_index_pe_lg(symbol=symbol).copy()
        history["日期"] = pd.to_datetime(history["日期"])
        history = history.sort_values("日期")
        pe_series = pd.to_numeric(
            history["滚动市盈率"], errors="coerce"
        ).dropna()
        current_pe = float(pe_series.iloc[-1])
        percentile = float((pe_series <= current_pe).mean() * 100)
        result[symbol] = {
            "index_code": code,
            "date": str(current["日期"]),
            "pe_ttm": round(current_pe, 2),
            "pe_percentile": round(percentile, 2),
            "csindex_pe_1": current.get("市盈率1"),
            "csindex_pe_2": current.get("市盈率2"),
            "history_start": str(history["日期"].iloc[0].date()),
            "history_count": int(len(pe_series)),
            "source": "scripts/_tmp_check_pe.py -> AKShare CSIndex + Legu",
        }
    return result


if __name__ == "__main__":
    for name, item in query_pe_snapshot().items():
        print(
            f"{name}: PE={item['pe_ttm']:.2f}, "
            f"percentile={item['pe_percentile']:.2f}%, "
            f"history_start={item['history_start']}, "
            f"count={item['history_count']}"
        )
        print(
            f"  csindex date={item['date']}, "
            f"pe1={item['csindex_pe_1']}, pe2={item['csindex_pe_2']}"
        )
