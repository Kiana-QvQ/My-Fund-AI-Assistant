"""Refresh fund status, index PE, percentile and a policy-based build plan.

This script is advice-only. It never places orders.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import akshare as ak
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "portfolio_policy.json"
DEFAULT_OUTPUT = ROOT / "data" / "market_snapshot.json"

FUNDS = [
    {
        "fund_code": "012773",
        "name": "嘉实超短债债券A",
        "asset": "short_bond",
        "target": 0.51,
        "index": None,
    },
    {
        "fund_code": "460300",
        "name": "华泰柏瑞沪深300ETF联接A",
        "asset": "a_share",
        "target": 0.27,
        "index": {"code": "000300", "name": "沪深300", "symbol": "沪深300"},
    },
    {
        "fund_code": "160119",
        "name": "南方中证500ETF联接(LOF)A",
        "asset": "a_share",
        "target": 0.11,
        "index": {"code": "000905", "name": "中证500", "symbol": "中证500"},
    },
    {
        "fund_code": "050025",
        "name": "博时标普500ETF联接A",
        "asset": "us",
        "target": 0.08,
        "index": {"name": "标普500", "symbol": None},
    },
    {
        "fund_code": "016452",
        "name": "南方纳斯达克100指数发起(QDII)A",
        "asset": "us",
        "target": 0.03,
        "index": {"name": "纳斯达克100", "symbol": None},
    },
]


def clean_number(value):
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fund_snapshot() -> dict[str, dict]:
    daily = ak.fund_open_fund_daily_em()
    purchase = ak.fund_purchase_em()
    result = {}
    for item in FUNDS:
        code = item["fund_code"]
        row = daily[daily["基金代码"].astype(str).str.zfill(6) == code]
        purchase_row = purchase[
            purchase["基金代码"].astype(str).str.zfill(6) == code
        ]
        daily_record = row.iloc[0].to_dict() if not row.empty else {}
        purchase_record = (
            purchase_row.iloc[0].to_dict() if not purchase_row.empty else {}
        )
        nav_columns = [
            c for c in daily.columns if str(c).endswith("单位净值")
        ]
        nav = None
        nav_date = None
        if daily_record:
            for column in nav_columns:
                value = clean_number(daily_record.get(column))
                if value is not None:
                    nav = value
                    nav_date = str(column).split("-单位净值")[0]
                    break
        result[code] = {
            "fund_code": code,
            "name": item["name"],
            "nav": nav
            if nav is not None
            else clean_number(purchase_record.get("最新净值/万份收益")),
            "nav_date": nav_date
            or str(purchase_record.get("最新净值/万份收益-报告时间", "")),
            "purchase_status": str(
                purchase_record.get("申购状态", daily_record.get("申购状态", "unknown"))
            ),
            "redemption_status": str(
                purchase_record.get("赎回状态", daily_record.get("赎回状态", "unknown"))
            ),
            "daily_limit": clean_number(purchase_record.get("日累计限定金额")),
            "minimum_purchase": clean_number(purchase_record.get("购买起点")),
            "fee_percent": clean_number(purchase_record.get("手续费")),
        }
    return result


def index_snapshot() -> dict[str, dict]:
    result = {}
    for symbol, index_code in (("沪深300", "000300"), ("中证500", "000905")):
        current = ak.stock_zh_index_value_csindex(symbol=index_code).iloc[0]
        history = ak.stock_index_pe_lg(symbol=symbol).copy()
        history["日期"] = pd.to_datetime(history["日期"])
        history = history.sort_values("日期")
        pe_series = pd.to_numeric(history["滚动市盈率"], errors="coerce").dropna()
        current_pe = float(pe_series.iloc[-1])
        percentile = float((pe_series <= current_pe).mean() * 100)
        result[symbol] = {
            "index_code": index_code,
            "date": str(current["日期"]),
            "pe_ttm": current_pe,
            "pe_percentile": round(percentile, 2),
            "csindex_pe_1": clean_number(current.get("市盈率1")),
            "csindex_pe_2": clean_number(current.get("市盈率2")),
            "history_start": str(history["日期"].iloc[0].date()),
            "history_count": int(len(pe_series)),
            "source": "AKShare: CSIndex + Legu index PE history",
        }
    result["标普500"] = {
        "pe_ttm": None,
        "pe_percentile": None,
        "status": "unavailable",
        "reason": "当前数据源未提供可验证的实时PE与历史分位，暂停自动判断",
    }
    result["纳斯达克100"] = {
        "pe_ttm": None,
        "pe_percentile": None,
        "status": "unavailable",
        "reason": "当前数据源未提供可验证的实时PE与历史分位，暂停自动判断",
    }
    return result


def action_for(item: dict, fund: dict, indexes: dict) -> tuple[str, str]:
    if item["asset"] == "short_bond":
        status = fund["purchase_status"]
        if status in ("开放申购", "限大额"):
            return "buy", "稳健底仓；当前申购状态允许小额建仓"
        return "wait", f"申购状态为 {status}"

    if fund["purchase_status"] == "暂停申购":
        return "wait", "基金当前暂停申购"
    if fund["purchase_status"] not in ("开放申购", "限大额"):
        return "wait", f"申购状态为 {fund['purchase_status']}"

    if item["asset"] == "a_share":
        index = indexes[item["index"]["symbol"]]
        p = index["pe_percentile"]
        if p <= 30:
            return "double", f"PE历史分位 {p:.2f}% <= 30%"
        if p < 40:
            return "buy", f"PE历史分位 {p:.2f}% < 40%"
        return "wait", f"PE历史分位 {p:.2f}% >= 40%，按政策暂停"

    return "wait", "美股指数PE和历史分位未核实，不自动买入"


def build_plan(principal: float, funds: list[dict], indexes: dict) -> dict:
    first_month = principal * 0.20
    allocations = []
    held_back = 0.0
    short_bond = next(item for item in funds if item["asset"] == "short_bond")

    for item in funds:
        fund = item["fund"]
        planned = first_month * item["target"]
        action, reason = action_for(item, fund, indexes)
        if action in ("wait",):
            held_back += planned
            planned = 0.0
        allocations.append(
            {
                "fund_code": item["fund_code"],
                "name": item["name"],
                "target_percent": item["target"] * 100,
                "action": action,
                "planned_amount": round(planned, 2),
                "reason": reason,
            }
        )

    short_plan = next(
        row for row in allocations if row["fund_code"] == short_bond["fund_code"]
    )
    if held_back and short_plan["action"] == "buy":
        short_plan["planned_amount"] = round(
            short_plan["planned_amount"] + held_back, 2
        )
        short_plan["reason"] += "；其余暂缓资金先留在短债底仓"

    return {
        "principal": principal,
        "initial_build_percent": 20,
        "first_month_budget": round(first_month, 2),
        "allocations": allocations,
        "deferred_amount": round(
            first_month - sum(row["planned_amount"] for row in allocations), 2
        ),
        "guardrail": "建议仅作研究计划，所有交易须人工核对申购状态和金额后执行",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="刷新基金和指数估值快照")
    parser.add_argument("--principal", type=float, default=10000)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    funds = fund_snapshot()
    indexes = index_snapshot()
    items = []
    for item in FUNDS:
        current = dict(item)
        current["fund"] = funds[item["fund_code"]]
        items.append(current)

    output = {
        "as_of": date.today().isoformat(),
        "funds": funds,
        "indexes": indexes,
        "build_plan": build_plan(args.principal, items, indexes),
    }
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(output_path)
    for row in output["build_plan"]["allocations"]:
        print(
            f"{row['fund_code']} {row['action']} "
            f"{row['planned_amount']:.2f} 元：{row['reason']}"
        )


if __name__ == "__main__":
    main()
