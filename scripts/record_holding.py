"""Record buy/sell into config/portfolio_holdings.json (advice ledger only).

Cost basis tracks invested principal (成本), not mark-to-market value.
Sell proceeds (市值回笼) and cost reduction (扣减成本) are recorded separately.

Amount vs shares×nav tolerance (申购费/四舍五入/小额费用):
  max(0.02 元, |amount| × 0.5%)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOLDINGS_PATH = ROOT / "config" / "portfolio_holdings.json"
CST = timezone(timedelta(hours=8))

# Allow small gaps from fees / rounding when both amount and shares×nav are given.
AMOUNT_MATCH_TOLERANCE_RATIO = 0.005
AMOUNT_MATCH_TOLERANCE_FLOOR = 0.02

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


def _fmt_num(value: float | None) -> str:
    if value is None:
        return ""
    return f"{float(value):.4f}"


def build_idempotency_key(
    *,
    side: str,
    fund_code: str,
    trade_date: str,
    amount: float | None = None,
    proceeds: float | None = None,
    cost: float | None = None,
    shares: float | None = None,
    note: str | None = None,
) -> str:
    raw = "|".join(
        [
            side,
            fund_code,
            trade_date,
            _fmt_num(amount),
            _fmt_num(proceeds),
            _fmt_num(cost),
            _fmt_num(shares),
            (note or "").strip(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def find_duplicate_tx(
    doc: dict,
    *,
    transaction_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict | None:
    for tx in doc.get("transactions") or []:
        if transaction_id and tx.get("transaction_id") == transaction_id:
            return tx
        if idempotency_key and tx.get("idempotency_key") == idempotency_key:
            return tx
    return None


def _prepare_tx_ids(
    doc: dict,
    payload: dict,
    *,
    transaction_id: str | None = None,
    force_duplicate: bool = False,
) -> tuple[str, str]:
    """Return (transaction_id, idempotency_key); raise if duplicate."""
    trade_date = date.today().isoformat()
    side = str(payload.get("side") or "")
    fund_code = str(payload.get("fund_code") or "")
    sell_cost = None
    if side == "sell" and isinstance(payload.get("cost_delta"), (int, float)):
        sell_cost = abs(float(payload["cost_delta"]))
    elif side == "set":
        sell_cost = payload.get("amount")
    idem_key = build_idempotency_key(
        side=side,
        fund_code=fund_code,
        trade_date=trade_date,
        amount=payload.get("amount"),
        proceeds=payload.get("proceeds"),
        cost=sell_cost,
        shares=payload.get("shares"),
        note=payload.get("note"),
    )
    tx_id = (transaction_id or "").strip() or str(uuid.uuid4())
    dup = find_duplicate_tx(doc, transaction_id=tx_id, idempotency_key=idem_key)
    if dup is not None and not force_duplicate:
        raise SystemExit(
            "检测到重复记账（同日同基金同金额/份额/备注，或相同 transaction_id）。"
            f" 已有流水 transaction_id={dup.get('transaction_id')}。"
            " 若确需重复入账请加 --force-duplicate。"
        )
    return tx_id, idem_key


def _append_tx(
    doc: dict,
    payload: dict,
    *,
    transaction_id: str,
    idempotency_key: str,
) -> dict:
    tx = {
        "transaction_id": transaction_id,
        "idempotency_key": idempotency_key,
        "trade_date": date.today().isoformat(),
        "time": datetime.now(CST).isoformat(timespec="seconds"),
        **payload,
    }
    doc.setdefault("transactions", []).append(tx)
    return tx


def _require_positive(label: str, value: float | None, *, allow_none: bool = True) -> None:
    if value is None:
        if allow_none:
            return
        raise SystemExit(f"{label} 不能为空，且必须 > 0")
    if value <= 0:
        raise SystemExit(f"{label} 必须 > 0，收到 {value}")


def _require_non_negative(label: str, value: float | None, *, allow_none: bool = True) -> None:
    if value is None:
        if allow_none:
            return
        raise SystemExit(f"{label} 不能为空，且必须 >= 0")
    if value < 0:
        raise SystemExit(f"{label} 必须 >= 0，收到 {value}")


def amount_match_tolerance(amount: float) -> float:
    return max(AMOUNT_MATCH_TOLERANCE_FLOOR, abs(amount) * AMOUNT_MATCH_TOLERANCE_RATIO)


def _assert_amount_matches_shares_nav(
    *,
    amount: float,
    shares: float,
    nav: float,
    side: str,
) -> None:
    expected = round(shares * nav, 2)
    tol = amount_match_tolerance(amount)
    if abs(expected - round(amount, 2)) > tol:
        raise SystemExit(
            f"{side}金额与份额×净值不一致：金额={amount:.2f}，"
            f"份额×净值={expected:.2f}（容差 {tol:.2f}，"
            f"约为 max({AMOUNT_MATCH_TOLERANCE_FLOOR:.2f}元, "
            f"|金额|×{AMOUNT_MATCH_TOLERANCE_RATIO * 100:.1f}%)，"
            "用于申购费/四舍五入/小额费用）"
        )


def apply_buy(
    doc: dict,
    fund_code: str,
    amount: float,
    note: str | None,
    *,
    shares: float | None = None,
    nav: float | None = None,
    transaction_id: str | None = None,
    force_duplicate: bool = False,
) -> dict:
    if fund_code not in CATALOG:
        raise SystemExit(f"未知基金代码: {fund_code}，可选: {', '.join(CATALOG)}")
    _require_positive("买入金额", amount, allow_none=False)
    _require_positive("买入份额", shares)
    _require_positive("买入净值", nav)

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
    if buy_shares is None and nav is not None:
        buy_shares = round(amount / nav, 4)
    if buy_shares is not None and nav is not None:
        _assert_amount_matches_shares_nav(
            amount=amount, shares=float(buy_shares), nav=float(nav), side="买入"
        )

    payload = {
        "side": "buy",
        "fund_code": fund_code,
        "amount": round(amount, 2),
        "cost_delta": round(amount, 2),
        "shares": buy_shares,
        "nav": nav,
        "note": note,
    }
    tx_id, idem_key = _prepare_tx_ids(
        doc,
        payload,
        transaction_id=transaction_id,
        force_duplicate=force_duplicate,
    )

    row["cost_basis"] = round(float(row.get("cost_basis") or 0) + amount, 2)
    if buy_shares is not None:
        prev_shares = row.get("shares")
        if prev_shares is None:
            row["shares"] = round(float(buy_shares), 4)
        else:
            row["shares"] = round(float(prev_shares) + float(buy_shares), 4)
    if note:
        row["note"] = note

    _append_tx(doc, payload, transaction_id=tx_id, idempotency_key=idem_key)
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
    transaction_id: str | None = None,
    force_duplicate: bool = False,
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

    _require_positive("赎回市值 proceeds", proceeds)
    _require_positive("扣减成本 cost", cost)
    _require_positive("卖出份额 shares", shares)
    _require_positive("卖出净值 nav", nav)

    sell_shares = shares
    if sell_shares is None and proceeds is not None and nav is not None:
        sell_shares = round(proceeds / nav, 4)

    if sell_shares is not None:
        if current_shares_f is None:
            raise SystemExit("持仓未记录份额，无法按份额卖出；请改用 --cost，或先 set 份额")
        if sell_shares > current_shares_f + 1e-9:
            raise SystemExit(
                f"卖出份额 {sell_shares} 超过持有份额 {current_shares_f}"
            )

    cost_reduction: float | None = cost
    if cost_reduction is None and sell_shares is not None and current_shares_f:
        cost_reduction = round(current_cost * (sell_shares / current_shares_f), 2)
    if cost_reduction is None:
        raise SystemExit(
            "卖出需指定 --cost（扣减成本）或 --shares（按持仓比例扣成本）；"
            "市值请用 --proceeds 单独记录"
        )
    if cost_reduction > current_cost + 1e-9:
        raise SystemExit(f"扣减成本 {cost_reduction} 超过已投入成本 {current_cost}")

    if (
        cost is not None
        and sell_shares is not None
        and current_shares_f
        and current_shares_f > 0
    ):
        expected_cost = round(current_cost * (sell_shares / current_shares_f), 2)
        tol = amount_match_tolerance(current_cost)
        if abs(expected_cost - cost_reduction) > tol:
            raise SystemExit(
                f"卖出成本与份额不成比例：--cost={cost_reduction:.2f}，"
                f"按持仓均摊应为 {expected_cost:.2f}（容差 {tol:.2f}）"
            )

    realized_proceeds = proceeds
    if realized_proceeds is None and sell_shares is not None and nav is not None:
        realized_proceeds = round(sell_shares * nav, 2)
    if realized_proceeds is not None and sell_shares is not None and nav is not None:
        _assert_amount_matches_shares_nav(
            amount=realized_proceeds,
            shares=float(sell_shares),
            nav=float(nav),
            side="卖出",
        )

    payload = {
        "side": "sell",
        "fund_code": fund_code,
        "proceeds": realized_proceeds,
        "cost_delta": round(-cost_reduction, 2),
        "shares": sell_shares,
        "nav": nav,
        "note": note,
    }
    tx_id, idem_key = _prepare_tx_ids(
        doc,
        payload,
        transaction_id=transaction_id,
        force_duplicate=force_duplicate,
    )

    row["cost_basis"] = round(current_cost - cost_reduction, 2)
    if sell_shares is not None and current_shares_f is not None:
        remaining = round(current_shares_f - float(sell_shares), 4)
        row["shares"] = remaining if remaining > 1e-9 else None
    if note:
        row["note"] = note

    _append_tx(doc, payload, transaction_id=tx_id, idempotency_key=idem_key)

    if row["cost_basis"] <= 0:
        holdings.remove(row)
    return row if row in holdings else {"fund_code": fund_code, "removed": True}


def apply_set(
    doc: dict,
    fund_code: str,
    cost: float,
    shares: float | None,
    note: str | None,
    *,
    transaction_id: str | None = None,
    force_duplicate: bool = False,
) -> dict:
    if fund_code not in CATALOG:
        raise SystemExit(f"未知基金代码: {fund_code}")
    _require_non_negative("覆盖成本", cost, allow_none=False)
    _require_non_negative("覆盖份额", shares)
    meta = CATALOG[fund_code]
    holdings = doc.setdefault("holdings", [])
    row = _find(holdings, fund_code)
    before_cost = float(row.get("cost_basis") or 0) if row else 0.0
    before_shares = row.get("shares") if row else None

    after_cost = round(cost, 2)
    after_shares = shares if shares is not None else (row.get("shares") if row else None)
    # Preview after values for existing row once applied.
    if row is not None:
        after_shares = shares if shares is not None else row.get("shares")

    payload = {
        "side": "set",
        "fund_code": fund_code,
        "amount": round(cost, 2),
        "cost_delta": round(after_cost - before_cost, 2),
        "before_cost": round(before_cost, 2),
        "after_cost": after_cost,
        "before_shares": before_shares,
        "after_shares": after_shares if row is not None else shares,
        "shares": shares,
        "nav": None,
        "note": note,
        "reason": note or "账本覆盖写入",
    }
    tx_id, idem_key = _prepare_tx_ids(
        doc,
        payload,
        transaction_id=transaction_id,
        force_duplicate=force_duplicate,
    )

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
        payload["after_shares"] = shares
    else:
        row["cost_basis"] = after_cost
        if shares is not None:
            row["shares"] = shares
        if note:
            row["note"] = note
        payload["after_shares"] = row.get("shares")
        payload["after_cost"] = float(row.get("cost_basis") or 0)
        payload["cost_delta"] = round(payload["after_cost"] - before_cost, 2)

    _append_tx(doc, payload, transaction_id=tx_id, idempotency_key=idem_key)
    if row["cost_basis"] <= 0:
        holdings.remove(row)
        return {"fund_code": fund_code, "removed": True}
    return row


def _add_common_tx_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tx-id",
        default=None,
        help="显式 transaction_id；重复提交同一 ID 会被拒绝（除非 --force-duplicate）",
    )
    parser.add_argument(
        "--force-duplicate",
        action="store_true",
        help="允许绕过同日幂等检测（仍建议换 note 或换 tx-id）",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "更新持仓账本 portfolio_holdings.json。"
            f" 金额与份额×净值容差为 max({AMOUNT_MATCH_TOLERANCE_FLOOR:.2f}元, "
            f"|金额|×{AMOUNT_MATCH_TOLERANCE_RATIO * 100:.1f}%)，"
            "用于申购费、四舍五入和小额费用。"
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    buy = sub.add_parser("buy", help="追加买入（增加成本）")
    buy.add_argument("--fund", required=True)
    buy.add_argument("--amount", type=float, required=True, help="买入金额=增加的成本")
    buy.add_argument("--shares", type=float, default=None)
    buy.add_argument(
        "--nav",
        type=float,
        default=None,
        help=(
            "买入净值；与 --shares 同时给出时校验金额≈份额×净值"
            f"（容差 max({AMOUNT_MATCH_TOLERANCE_FLOOR:.2f}, |金额|×"
            f"{AMOUNT_MATCH_TOLERANCE_RATIO * 100:.1f}%））"
        ),
    )
    buy.add_argument("--note", default=None)
    _add_common_tx_args(buy)

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
    _add_common_tx_args(sell)

    sett = sub.add_parser("set", help="覆盖写入某基金成本（流水记录前后值）")
    sett.add_argument("--fund", required=True)
    sett.add_argument("--cost", type=float, required=True)
    sett.add_argument("--shares", type=float, default=None)
    sett.add_argument("--note", default=None, help="操作原因，会写入流水 reason")
    _add_common_tx_args(sett)

    show = sub.add_parser("show", help="打印当前账本")

    args = parser.parse_args()
    doc = load_holdings()
    if args.command == "show":
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return

    tx_kwargs = {
        "transaction_id": getattr(args, "tx_id", None),
        "force_duplicate": bool(getattr(args, "force_duplicate", False)),
    }
    if args.command == "buy":
        row = apply_buy(
            doc,
            args.fund,
            args.amount,
            args.note,
            shares=args.shares,
            nav=args.nav,
            **tx_kwargs,
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
            **tx_kwargs,
        )
    else:
        row = apply_set(
            doc, args.fund, args.cost, args.shares, args.note, **tx_kwargs
        )
    save_holdings(doc)
    print(f"已更新 {HOLDINGS_PATH}")
    print(json.dumps(row, ensure_ascii=False, indent=2))
    if doc.get("transactions"):
        print(
            "最近流水 transaction_id="
            + str(doc["transactions"][-1].get("transaction_id"))
        )


if __name__ == "__main__":
    main()
