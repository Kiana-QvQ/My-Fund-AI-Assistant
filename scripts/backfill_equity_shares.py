"""Backfill equity buy txs with historical NAV / estimated shares; recompute holdings."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
ROOT = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from cost_basis_metrics import avg_cost  # noqa: E402
from fund_nav import (  # noqa: E402
    EQUITY_COST_FUNDS,
    ensure_nav_range,
    estimate_shares,
    fee_percent_from_snapshot,
    lookup_nav_on_or_before,
)
from record_holding import HOLDINGS_PATH, load_holdings, save_holdings  # noqa: E402


SNAPSHOT_PATH = ROOT / "data" / "market_snapshot.json"


def _load_snapshot() -> dict:
    if not SNAPSHOT_PATH.is_file():
        return {}
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def backfill_equity_shares(
    doc: dict,
    *,
    fund_codes: tuple[str, ...] = EQUITY_COST_FUNDS,
    snapshot: dict | None = None,
    refresh_nav: bool = False,
    force: bool = False,
) -> dict:
    """Fill nav/shares on equity buys lacking them; refresh holding share totals."""
    snapshot = snapshot if snapshot is not None else _load_snapshot()
    txs = list(doc.get("transactions") or [])
    codes = tuple(str(c) for c in fund_codes)
    touched = 0
    skipped = 0
    missing_nav = []

    for code in codes:
        days = [
            str(tx.get("trade_date") or "")[:10]
            for tx in txs
            if tx.get("side") == "buy" and str(tx.get("fund_code") or "") == code
        ]
        days = [d for d in days if d]
        if not days:
            continue
        start, end = min(days), max(days)
        series = ensure_nav_range(code, start=start, end=end, refresh=refresh_nav)
        fee = fee_percent_from_snapshot(snapshot, code)

        for tx in txs:
            if tx.get("side") != "buy":
                continue
            if str(tx.get("fund_code") or "") != code:
                continue
            trade_date = str(tx.get("trade_date") or "")[:10]
            amount = float(tx.get("amount") or tx.get("cost_delta") or 0)
            if amount <= 0 or not trade_date:
                skipped += 1
                continue
            has_nav = isinstance(tx.get("nav"), (int, float)) and float(tx["nav"]) > 0
            has_shares = (
                isinstance(tx.get("shares"), (int, float)) and float(tx["shares"]) > 0
            )
            if has_nav and has_shares and not force:
                skipped += 1
                continue

            hit = lookup_nav_on_or_before(series, trade_date)
            if hit is None:
                missing_nav.append({"fund_code": code, "trade_date": trade_date})
                continue
            nav_date, nav = hit
            shares = estimate_shares(amount, nav, fee_percent=fee)
            tx["nav"] = round(float(nav), 6)
            tx["shares"] = shares
            tx["nav_date"] = nav_date
            tx["shares_source"] = "estimated_nav"
            if nav_date != trade_date:
                tx["nav_match"] = f"on_or_before:{nav_date}"
            else:
                tx["nav_match"] = "exact"
            touched += 1

    # Recompute holding shares for equity codes from buy txs (sells rare / none).
    holdings = doc.setdefault("holdings", [])
    by_code = {str(h.get("fund_code")): h for h in holdings}
    for code in codes:
        total_shares = 0.0
        for tx in txs:
            if tx.get("side") != "buy":
                continue
            if str(tx.get("fund_code") or "") != code:
                continue
            if isinstance(tx.get("shares"), (int, float)):
                total_shares += float(tx["shares"])
        row = by_code.get(code)
        if row is None:
            continue
        if total_shares > 0:
            row["shares"] = round(total_shares, 4)
            average = avg_cost(row.get("cost_basis"), row["shares"])
            row["avg_cost"] = average
            row["shares_source"] = "sum_buy_estimated"
            row["avg_cost_updated_at"] = datetime.now().isoformat(timespec="seconds")

    doc["transactions"] = txs
    return {
        "touched_txs": touched,
        "skipped_txs": skipped,
        "missing_nav": missing_nav,
        "holdings": [
            {
                "fund_code": code,
                "cost_basis": (by_code.get(code) or {}).get("cost_basis"),
                "shares": (by_code.get(code) or {}).get("shares"),
                "avg_cost": (by_code.get(code) or {}).get("avg_cost"),
            }
            for code in codes
            if code in by_code
        ],
    }


def align_holding_shares(
    doc: dict,
    fund_code: str,
    shares: float,
    *,
    note: str | None = None,
) -> dict:
    """Broker-confirmed share override for one holding (does not rewrite txs)."""
    code = str(fund_code)
    for row in doc.setdefault("holdings", []):
        if str(row.get("fund_code") or "") != code:
            continue
        row["shares"] = round(float(shares), 4)
        row["avg_cost"] = avg_cost(row.get("cost_basis"), row["shares"])
        row["shares_source"] = "broker_aligned"
        row["avg_cost_updated_at"] = datetime.now().isoformat(timespec="seconds")
        if note:
            row["align_note"] = note
        return row
    raise SystemExit(f"持仓中未找到基金 {code}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="用历史净值回填权益买入份额，并刷新持仓均价"
    )
    parser.add_argument(
        "--refresh-nav",
        action="store_true",
        help="强制重新拉取净值缓存",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖已有 nav/shares",
    )
    parser.add_argument(
        "--align",
        nargs=2,
        metavar=("FUND", "SHARES"),
        help="券商对齐：覆盖某基金持仓总份额，例如 --align 016452 61.23",
    )
    parser.add_argument(
        "--align-note",
        default=None,
        help="券商对齐备注",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印结果，不写账本",
    )
    args = parser.parse_args()
    doc = load_holdings()

    result: dict = {}
    if args.align:
        fund, shares_s = args.align
        row = align_holding_shares(
            doc, fund, float(shares_s), note=args.align_note
        )
        result["aligned"] = row
    else:
        result = backfill_equity_shares(
            doc,
            refresh_nav=bool(args.refresh_nav),
            force=bool(args.force),
        )

    if not args.dry_run:
        save_holdings(doc)
        result["saved"] = str(HOLDINGS_PATH)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
