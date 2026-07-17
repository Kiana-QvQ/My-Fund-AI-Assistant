"""Refresh fund status, index PE, percentile and a policy-based build plan.

This script is advice-only. It never places orders.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import akshare as ak
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a_share_pe import query_pe_snapshot  # noqa: E402
from policy_rules import classify_index, load_policy  # noqa: E402
from qdii_premium import fetch_qdii_premiums  # noqa: E402
from us_pe import refresh_us_pe  # noqa: E402

DEFAULT_OUTPUT = ROOT / "data" / "market_snapshot.json"
HOLDINGS_PATH = ROOT / "config" / "portfolio_holdings.json"
US_PE_PATH = ROOT / "config" / "us_pe_snapshot.json"

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
        "index": {"name": "标普500", "symbol": None, "etf": "513500"},
    },
    {
        "fund_code": "016452",
        "name": "南方纳斯达克100指数发起(QDII)A",
        "asset": "us",
        "target": 0.03,
        "index": {"name": "纳斯达克100", "symbol": None, "etf": "159941"},
    },
]


def clean_number(value):
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_holdings_cost() -> dict[str, float]:
    if not HOLDINGS_PATH.is_file():
        return {}
    doc = json.loads(HOLDINGS_PATH.read_text(encoding="utf-8"))
    return {
        item["fund_code"]: float(item.get("cost_basis") or 0)
        for item in doc.get("holdings", [])
    }


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


def index_snapshot() -> tuple[dict[str, dict], dict[str, dict]]:
    result = query_pe_snapshot()
    try:
        us_snapshot = refresh_us_pe()
    except Exception as exc:
        us_snapshot = json.loads(US_PE_PATH.read_text(encoding="utf-8"))
        us_snapshot["refresh_error"] = str(exc)
    for name, item in us_snapshot.get("indexes", {}).items():
        result[name] = {
            **item,
            "date": us_snapshot.get("as_of"),
            "window": us_snapshot.get("window"),
            "source": us_snapshot.get("source"),
        }
    try:
        premiums = fetch_qdii_premiums()
    except Exception as exc:
        premiums = {
            "标普500": {"premium": None, "status": "error", "reason": str(exc)},
            "纳斯达克100": {"premium": None, "status": "error", "reason": str(exc)},
        }
    for name, premium_item in premiums.items():
        if name in result:
            result[name]["qdii_premium"] = premium_item.get("premium")
            result[name]["qdii_premium_pct"] = premium_item.get("premium_pct")
            result[name]["qdii_etf"] = premium_item.get("etf_code")
            result[name]["qdii_premium_status"] = premium_item.get("status")
            result[name]["qdii_premium_reason"] = premium_item.get("reason")
    return result, premiums


def action_for(
    item: dict,
    fund: dict,
    indexes: dict,
    holdings_cost: dict[str, float],
    policy: dict,
) -> tuple[str, str]:
    if item["asset"] == "short_bond":
        status = fund["purchase_status"]
        if status in ("开放申购", "限大额"):
            return "buy", "稳健底仓；当前申购状态允许小额建仓"
        return "wait", f"申购状态为 {status}"

    if fund["purchase_status"] == "暂停申购":
        # Still surface take-profit / premium notes via index rules when useful.
        index_name = (
            item["index"]["symbol"]
            if item["asset"] == "a_share"
            else item["index"]["name"]
        )
        index = indexes.get(index_name, {})
        signal, reason = classify_index(
            index_name,
            index.get("pe_percentile"),
            premium=index.get("qdii_premium"),
            policy=policy,
        )
        if signal == "take_profit" and holdings_cost.get(item["fund_code"], 0) > 0:
            return "take_profit", f"{reason}；基金暂停申购不影响止盈观察"
        return "wait", f"基金当前暂停申购；{reason}"

    if fund["purchase_status"] not in ("开放申购", "限大额"):
        return "wait", f"申购状态为 {fund['purchase_status']}"

    if item["asset"] == "a_share":
        index_name = item["index"]["symbol"]
    else:
        index_name = item["index"]["name"]
    index = indexes.get(index_name, {})
    signal, reason = classify_index(
        index_name,
        index.get("pe_percentile"),
        premium=index.get("qdii_premium"),
        policy=policy,
    )
    if signal == "take_profit":
        held = holdings_cost.get(item["fund_code"], 0.0)
        if held <= 0:
            return "wait", f"{reason}；账本暂无该基金持仓，仅观察"
        low = round(held / 3, 2)
        high = round(held / 2, 2)
        return "take_profit", f"{reason}；建议赎回约 {low:.2f}~{high:.2f} 元"
    if signal in ("buy", "double"):
        return signal, reason
    return "wait", reason


def build_plan(
    principal: float,
    funds: list[dict],
    indexes: dict,
    holdings_cost: dict[str, float],
    policy: dict,
) -> dict:
    first_month = principal * 0.20
    allocations = []
    held_back = 0.0
    double_extra = 0.0
    take_profit_notes: list[str] = []
    short_bond = next(item for item in funds if item["asset"] == "short_bond")

    for item in funds:
        fund = item["fund"]
        base = first_month * item["target"]
        action, reason = action_for(item, fund, indexes, holdings_cost, policy)
        planned = base
        if action in ("wait", "take_profit", "premium_block", "unknown"):
            if action != "take_profit":
                held_back += base
            else:
                held_back += base
                take_profit_notes.append(reason)
            planned = 0.0
        elif action == "double":
            planned = base * 2
            double_extra += base
            reason = f"{reason}；加倍部分从短债底仓调拨"
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
    if short_plan["action"] == "buy":
        adjusted = short_plan["planned_amount"] + held_back - double_extra
        short_plan["planned_amount"] = round(max(adjusted, 0.0), 2)
        notes = []
        if held_back:
            notes.append("权益暂停/止盈观察资金转入短债底仓")
        if double_extra:
            notes.append(f"已为低估加倍调出 {double_extra:.2f} 元")
        if notes:
            short_plan["reason"] += "；" + "；".join(notes)

    return {
        "principal": principal,
        "initial_build_percent": 20,
        "first_month_budget": round(first_month, 2),
        "allocations": allocations,
        "take_profit_notes": take_profit_notes,
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

    policy = load_policy()
    funds = fund_snapshot()
    indexes, premiums = index_snapshot()
    holdings_cost = load_holdings_cost()
    items = []
    for item in FUNDS:
        current = dict(item)
        current["fund"] = funds[item["fund_code"]]
        items.append(current)

    output = {
        "as_of": date.today().isoformat(),
        "funds": funds,
        "indexes": indexes,
        "qdii_premiums": premiums,
        "build_plan": build_plan(
            args.principal, items, indexes, holdings_cost, policy
        ),
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
