"""Record buy/sell into config/portfolio_holdings.json (advice ledger only)."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOLDINGS_PATH = ROOT / "config" / "portfolio_holdings.json"

CATALOG = {
    "012773": {
        "name": "嘉实超短债债券A",
        "target_percent": 51.0,
        "asset_class": "短债基金",
    },
    "460300": {
        "name": "华泰柏瑞沪深300ETF联接A",
        "target_percent": 27.0,
        "asset_class": "A股宽基",
    },
    "160119": {
        "name": "南方中证500ETF联接(LOF)A",
        "target_percent": 11.0,
        "asset_class": "A股宽基",
    },
    "050025": {
        "name": "博时标普500ETF联接A",
        "target_percent": 8.0,
        "asset_class": "美股QDII",
    },
    "016452": {
        "name": "南方纳斯达克100指数发起(QDII)A",
        "target_percent": 3.0,
        "asset_class": "美股QDII",
    },
}


def load_holdings() -> dict:
    if not HOLDINGS_PATH.is_file():
        return {
            "as_of": date.today().isoformat(),
            "base_currency": "CNY",
            "building_principal": 10000.0,
            "initial_build_percent": 20.0,
            "holdings": [],
        }
    return json.loads(HOLDINGS_PATH.read_text(encoding="utf-8"))


def save_holdings(doc: dict) -> None:
    doc["as_of"] = date.today().isoformat()
    HOLDINGS_PATH.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _find(holdings: list[dict], fund_code: str) -> dict | None:
    for item in holdings:
        if item.get("fund_code") == fund_code:
            return item
    return None


def apply_buy(doc: dict, fund_code: str, amount: float, note: str | None) -> dict:
    if fund_code not in CATALOG:
        raise SystemExit(f"未知基金代码: {fund_code}，可选: {', '.join(CATALOG)}")
    meta = CATALOG[fund_code]
    holdings = doc.setdefault("holdings", [])
    row = _find(holdings, fund_code)
    if row is None:
        row = {
            "fund_code": fund_code,
            "name": meta["name"],
            "shares": None,
            "cost_basis": 0.0,
            "target_percent": meta["target_percent"],
            "asset_class": meta["asset_class"],
            "note": note or "账本记录",
        }
        holdings.append(row)
    row["cost_basis"] = round(float(row.get("cost_basis") or 0) + amount, 2)
    if note:
        row["note"] = note
    return row


def apply_sell(doc: dict, fund_code: str, amount: float, note: str | None) -> dict:
    holdings = doc.setdefault("holdings", [])
    row = _find(holdings, fund_code)
    if row is None:
        raise SystemExit(f"账本中没有 {fund_code}，无法卖出")
    current = float(row.get("cost_basis") or 0)
    if amount > current + 1e-9:
        raise SystemExit(f"卖出金额 {amount} 超过已投入 {current}")
    row["cost_basis"] = round(current - amount, 2)
    if note:
        row["note"] = note
    if row["cost_basis"] <= 0:
        holdings.remove(row)
    return row


def apply_set(
    doc: dict,
    fund_code: str,
    cost: float,
    shares: float | None,
    note: str | None,
) -> dict:
    if fund_code not in CATALOG:
        raise SystemExit(f"未知基金代码: {fund_code}")
    meta = CATALOG[fund_code]
    holdings = doc.setdefault("holdings", [])
    row = _find(holdings, fund_code)
    if row is None:
        row = {
            "fund_code": fund_code,
            "name": meta["name"],
            "shares": shares,
            "cost_basis": cost,
            "target_percent": meta["target_percent"],
            "asset_class": meta["asset_class"],
            "note": note or "账本覆盖写入",
        }
        holdings.append(row)
    else:
        row["cost_basis"] = round(cost, 2)
        if shares is not None:
            row["shares"] = shares
        if note:
            row["note"] = note
    if row["cost_basis"] <= 0:
        holdings.remove(row)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="更新持仓账本 portfolio_holdings.json")
    sub = parser.add_subparsers(dest="command", required=True)

    buy = sub.add_parser("buy", help="追加买入金额")
    buy.add_argument("--fund", required=True)
    buy.add_argument("--amount", type=float, required=True)
    buy.add_argument("--note", default=None)

    sell = sub.add_parser("sell", help="减少投入金额（止盈/赎回）")
    sell.add_argument("--fund", required=True)
    sell.add_argument("--amount", type=float, required=True)
    sell.add_argument("--note", default=None)

    sett = sub.add_parser("set", help="覆盖写入某基金成本")
    sett.add_argument("--fund", required=True)
    sett.add_argument("--cost", type=float, required=True)
    sett.add_argument("--shares", type=float, default=None)
    sett.add_argument("--note", default=None)

    show = sub.add_parser("show", help="打印当前账本")

    args = parser.parse_args()
    doc = load_holdings()
    if args.command == "show":
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return
    if args.command == "buy":
        row = apply_buy(doc, args.fund, args.amount, args.note)
    elif args.command == "sell":
        row = apply_sell(doc, args.fund, args.amount, args.note)
    else:
        row = apply_set(doc, args.fund, args.cost, args.shares, args.note)
    save_holdings(doc)
    print(f"已更新 {HOLDINGS_PATH}")
    print(json.dumps(row, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
