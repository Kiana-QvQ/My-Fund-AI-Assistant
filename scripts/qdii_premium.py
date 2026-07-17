"""Fetch onshore QDII ETF premium vs IOPV for policy gate."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import akshare as ak
import pandas as pd


CST = timezone(timedelta(hours=8))

# Field ETF codes used for premium checks (场内).
QDII_ETF = {
    "标普500": "513500",
    "纳斯达克100": "159941",
}


def _to_float(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_qdii_premiums(codes: dict[str, str] | None = None) -> dict[str, dict]:
    codes = codes or QDII_ETF
    spot = ak.fund_etf_spot_em()
    spot["代码"] = spot["代码"].astype(str).str.zfill(6)
    result: dict[str, dict] = {}
    for name, code in codes.items():
        row = spot[spot["代码"] == code]
        if row.empty:
            result[name] = {
                "etf_code": code,
                "premium": None,
                "status": "missing",
                "reason": f"未找到 ETF {code} 行情",
            }
            continue
        item = row.iloc[0]
        price = _to_float(item.get("最新价"))
        iopv = _to_float(item.get("IOPV实时估值"))
        if price is None or iopv is None or iopv == 0:
            result[name] = {
                "etf_code": code,
                "price": price,
                "iopv": iopv,
                "premium": None,
                "status": "incomplete",
                "reason": "缺少最新价或 IOPV",
            }
            continue
        premium = (price - iopv) / iopv
        result[name] = {
            "etf_code": code,
            "price": round(price, 4),
            "iopv": round(iopv, 4),
            "premium": round(premium, 4),
            "premium_pct": round(premium * 100, 2),
            "status": "ok",
            "as_of": datetime.now(CST).isoformat(timespec="seconds"),
            "reason": f"场内{code} 溢价 {premium * 100:.2f}%",
        }
    return result


if __name__ == "__main__":
    import json

    print(json.dumps(fetch_qdii_premiums(), ensure_ascii=False, indent=2))
