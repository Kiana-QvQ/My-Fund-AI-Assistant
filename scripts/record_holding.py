"""Record buy/sell into config/portfolio_holdings.json (advice ledger only).

Cost basis tracks invested principal (成本), not mark-to-market value.
Sell proceeds (市值回笼) and cost reduction (扣减成本) are recorded separately.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOLDINGS_PATH = ROOT / "config" / "portfolio_holdings.json"
CST = timezone(timedelta(hours=8))

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
            "transactions": [],
        }
    doc = json.loads(HOLDINGS_PATH.read_text(encoding="utf-8"))
    doc.setdefault("transactions", [])
    return doc


def save_holdings(doc: dict) -> None:
    doc["as_of"] = date.today().isoformat()
    doc.setdefault("transactions", [])
    HOLDINGS_PATH.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _find(holdings: list[dict], fund_code: str) -> dict | None:
    for item in holdings:
        if item.get("fund_code") == fund_code:
            return item
    return None


def _append_tx(doc: dict, payload: dict) -> None:
    tx = {
        "time": datetime.now(CST).isoformat(timespec="seconds"),
        **payload,
    }
    doc.setdefault("transactions", []).append(tx)


def apply_buy(
    doc: dict,
    fund_code: str,
    amount: float,
    note: str | None,
    *,
    shares: float | None = None,
    nav: float | None = None,
) -> dict:
    if fund_code not in CATALOG:
        raise SystemExit(f"未知基金代码: {fund_code}，可选: {', '.join(CATALOG)}")
    if amount <= 0:
        raise SystemExit("买入金额必须 > 0")
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

    buy_shares = shares
    if buy_shares is None and nav is not None and nav > 0:
        buy_shares = round(amount / nav, 4)

    row["cost_basis"] = round(float(row.get("cost_basis") or 0) + amount, 2)
    if buy_shares is not None:
        prev_shares = row.get("shares")
        if prev_shares is None:
            row["shares"] = round(float(buy_shares), 4)
        else:
            row["shares"] = round(float(prev_shares) + float(buy_shares), 4)
    if note:
        row["note"] = note

    _append_tx(
        doc,
        {
            "side": "buy",
            "fund_code": fund_code,
            "amount": round(amount, 2),
            "cost_delta": round(amount, 2),
            "shares": buy_shares,
            "nav": nav,
            "note": note,
        },
    )
    return row


def apply_sell(
    doc: dict,
    fund_code: str,
    *,
    proceeds: float | None = None,
    cost: float | None = None,
    shares: float | None = None,
    nav: float | None = None,
    note: str | None = None,
    legacy_amount: float | None = None,
) -> dict:
    """Reduce cost basis by sold cost; record market proceeds separately.

    Preferred:
      --proceeds 市值回笼 --cost 对应成本
      --proceeds 市值回笼 --shares 份额（按持仓均摊成本）
    Legacy:
      --amount 视为扣减成本（不是市值），会打印警告。
    """
    holdings = doc.setdefault("holdings", [])
    row = _find(holdings, fund_code)
    if row is None:
        raise SystemExit(f"账本中没有 {fund_code}，无法卖出")

    current_cost = float(row.get("cost_basis") or 0)
    current_shares = row.get("shares")
    current_shares_f = float(current_shares) if current_shares is not None else None

    if legacy_amount is not None and cost is None and shares is None:
        print(
            "警告: --amount 已按「扣减成本」处理，不等于赎回市值。"
            "请改用 --proceeds 与 --cost/--shares。",
            file=sys.stderr,
        )
        cost = legacy_amount

    sell_shares = shares
    if sell_shares is None and proceeds is not None and nav is not None and nav > 0:
        sell_shares = round(proceeds / nav, 4)

    cost_reduction: float | None = cost
    if cost_reduction is None and sell_shares is not None and current_shares_f:
        if sell_shares > current_shares_f + 1e-9:
            raise SystemExit(
                f"卖出份额 {sell_shares} 超过持有份额 {current_shares_f}"
            )
        cost_reduction = round(current_cost * (sell_shares / current_shares_f), 2)
    if cost_reduction is None:
        raise SystemExit(
            "卖出需指定 --cost（扣减成本）或 --shares（按持仓比例扣成本）；"
            "市值请用 --proceeds 单独记录"
        )
    if cost_reduction <= 0:
        raise SystemExit("扣减成本必须 > 0")
    if cost_reduction > current_cost + 1e-9:
        raise SystemExit(f"扣减成本 {cost_reduction} 超过已投入成本 {current_cost}")

    realized_proceeds = proceeds
    if realized_proceeds is None and sell_shares is not None and nav is not None:
        realized_proceeds = round(sell_shares * nav, 2)

    row["cost_basis"] = round(current_cost - cost_reduction, 2)
    if sell_shares is not None and current_shares_f is not None:
        remaining = round(current_shares_f - float(sell_shares), 4)
        row["shares"] = remaining if remaining > 1e-9 else None
    if note:
        row["note"] = note

    _append_tx(
        doc,
        {
            "side": "sell",
            "fund_code": fund_code,
            "proceeds": realized_proceeds,
            "cost_delta": round(-cost_reduction, 2),
            "shares": sell_shares,
            "nav": nav,
            "note": note,
        },
    )

    if row["cost_basis"] <= 0:
        holdings.remove(row)
    return row if row in holdings else {"fund_code": fund_code, "removed": True}


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
    _append_tx(
        doc,
        {
            "side": "set",
            "fund_code": fund_code,
            "amount": round(cost, 2),
            "cost_delta": None,
            "shares": shares,
            "nav": None,
            "note": note,
        },
    )
    if row["cost_basis"] <= 0:
        holdings.remove(row)
        return {"fund_code": fund_code, "removed": True}
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="更新持仓账本 portfolio_holdings.json")
    sub = parser.add_subparsers(dest="command", required=True)

    buy = sub.add_parser("buy", help="追加买入（增加成本）")
    buy.add_argument("--fund", required=True)
    buy.add_argument("--amount", type=float, required=True, help="买入金额=增加的成本")
    buy.add_argument("--shares", type=float, default=None)
    buy.add_argument("--nav", type=float, default=None, help="买入净值；可与金额推算份额")
    buy.add_argument("--note", default=None)

    sell = sub.add_parser(
        "sell",
        help="卖出/赎回：--proceeds 记市值，--cost/--shares 扣成本",
    )
    sell.add_argument("--fund", required=True)
    sell.add_argument("--proceeds", type=float, default=None, help="赎回到账市值（不直接减成本）")
    sell.add_argument("--cost", type=float, default=None, help="本次应扣减的成本本金")
    sell.add_argument("--shares", type=float, default=None, help="卖出份额（按持仓均摊扣成本）")
    sell.add_argument("--nav", type=float, default=None, help="卖出净值")
    sell.add_argument(
        "--amount",
        type=float,
        default=None,
        help="兼容旧用法：视为 --cost（扣减成本），不是市值",
    )
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
        row = apply_buy(
            doc,
            args.fund,
            args.amount,
            args.note,
            shares=args.shares,
            nav=args.nav,
        )
    elif args.command == "sell":
        row = apply_sell(
            doc,
            args.fund,
            proceeds=args.proceeds,
            cost=args.cost,
            shares=args.shares,
            nav=args.nav,
            note=args.note,
            legacy_amount=args.amount,
        )
    else:
        row = apply_set(doc, args.fund, args.cost, args.shares, args.note)
    save_holdings(doc)
    print(f"已更新 {HOLDINGS_PATH}")
    print(json.dumps(row, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
