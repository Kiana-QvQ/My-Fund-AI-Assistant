"""Auto-ledger personal trading-day DCA schedules into portfolio_holdings.json.

Policy `dca.personal_schedules` describes broker auto-invest rules (e.g. Nasdaq
016452 ¥10 each A-share trading day, stop at ¥215/month). This script mirrors
those installs into the advice ledger so README / GitHub stay current without
manual daily `record_holding.py` calls.

Behavior:
- Only enabled schedules with trading_day cadence
- If the fund already has at least one purpose=dca buy: backfill every missing
  A-share trading day from the day after the last personal DCA through --as-of
- If no personal DCA history yet: only book --as-of (avoid inventing a full month)
- Respects monthly_stop_amount; idempotent per date+fund+amount+note
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

_SCRIPTS = Path(__file__).resolve().parent
ROOT = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from investment_plan import (  # noqa: E402
    personal_dca_line,
    personal_dca_schedules,
)
from policy_rules import load_policy  # noqa: E402
from record_holding import (  # noqa: E402
    HOLDINGS_PATH,
    apply_buy,
    load_holdings,
    parse_trade_date,
    save_holdings,
    today_cst,
)


IsTradingDay = Callable[[date], bool]


def default_is_trading_day(day: date) -> bool:
    """Prefer AKShare A-share calendar; fall back to Mon–Fri if unavailable."""
    try:
        from trading_calendar import is_a_share_trading_day

        return bool(is_a_share_trading_day(day))
    except Exception as exc:  # noqa: BLE001 — local env / numpy drift
        if day.weekday() >= 5:
            return False
        # Import failures often dump a long traceback before raising; keep one line.
        msg = f"{type(exc).__name__}: {exc}"
        print(
            f"警告: 交易日历不可用（{msg}），{day.isoformat()} 按工作日回退处理",
            file=sys.stderr,
        )
        return True


def personal_dca_note(schedule: dict, fund_code: str) -> str:
    name = str(schedule.get("name") or fund_code).strip()
    return f"{name}交易日定投"


def is_personal_dca_tx(tx: dict, fund_code: str) -> bool:
    if tx.get("side") != "buy":
        return False
    if str(tx.get("fund_code") or "") != str(fund_code):
        return False
    purpose = str(tx.get("purpose") or "").strip().lower()
    note = str(tx.get("note") or "")
    if purpose == "dca":
        return True
    return "交易日定投" in note


def personal_dca_month_spent(
    doc: dict,
    fund_code: str,
    month_key: str,
) -> float:
    total = 0.0
    for tx in doc.get("transactions") or []:
        if not is_personal_dca_tx(tx, fund_code):
            continue
        if str(tx.get("trade_date") or "")[:7] != month_key:
            continue
        total += float(tx.get("amount") or tx.get("cost_delta") or 0)
    return round(total, 2)


def last_personal_dca_date(doc: dict, fund_code: str) -> date | None:
    latest: date | None = None
    for tx in doc.get("transactions") or []:
        if not is_personal_dca_tx(tx, fund_code):
            continue
        raw = str(tx.get("trade_date") or "")[:10]
        if not raw:
            continue
        day = datetime.strptime(raw, "%Y-%m-%d").date()
        if latest is None or day > latest:
            latest = day
    return latest


def has_personal_dca_on(doc: dict, fund_code: str, day: date) -> bool:
    key = day.isoformat()
    for tx in doc.get("transactions") or []:
        if not is_personal_dca_tx(tx, fund_code):
            continue
        if str(tx.get("trade_date") or "")[:10] == key:
            return True
    return False


def iter_calendar_days(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def sync_personal_dca(
    doc: dict,
    *,
    as_of: date | None = None,
    policy: dict | None = None,
    is_trading_day: IsTradingDay | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Book missing personal DCA installs into ``doc``; return booked rows."""
    as_of = as_of or today_cst()
    policy = policy or load_policy()
    check = is_trading_day or default_is_trading_day
    booked: list[dict] = []

    for fund_code, schedule in personal_dca_schedules(policy).items():
        if not schedule.get("enabled", True):
            continue
        if schedule.get("auto_ledger", True) is False:
            continue
        if str(schedule.get("schedule") or "trading_day") not in (
            "trading_day",
            "daily",
        ):
            continue

        note = personal_dca_note(schedule, fund_code)
        last = last_personal_dca_date(doc, fund_code)
        if last is None:
            start = as_of
        else:
            start = last + timedelta(days=1)
        if start > as_of:
            continue

        for day in iter_calendar_days(start, as_of):
            trading = bool(check(day))
            month_key = day.isoformat()[:7]
            spent = personal_dca_month_spent(doc, fund_code, month_key)
            line = personal_dca_line(
                fund_code,
                month_spent=spent,
                is_trading_day=trading,
                policy=policy,
            )
            if not line.get("executable"):
                continue
            if has_personal_dca_on(doc, fund_code, day):
                continue

            amount = float(line["today_amount"])
            entry = {
                "fund_code": fund_code,
                "trade_date": day.isoformat(),
                "amount": amount,
                "note": note,
                "purpose": "dca",
                "reason": line.get("reason"),
            }
            if dry_run:
                booked.append(entry)
                # Simulate spend so later days in the same dry-run respect the cap.
                doc.setdefault("transactions", []).append(
                    {
                        "side": "buy",
                        "fund_code": fund_code,
                        "trade_date": day.isoformat(),
                        "amount": amount,
                        "purpose": "dca",
                        "note": note,
                    }
                )
                continue

            apply_buy(
                doc,
                fund_code,
                amount,
                note,
                purpose="dca",
                trade_date=day,
            )
            booked.append(entry)

    return booked


def main() -> None:
    parser = argparse.ArgumentParser(
        description="按 personal_schedules 自动补记交易日个人定投到账本"
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="补记截止日 YYYY-MM-DD（默认今天 CST）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要入账的流水，不写文件",
    )
    args = parser.parse_args()
    as_of = parse_trade_date(args.as_of) if args.as_of else today_cst()
    doc = load_holdings()
    # Dry-run mutates a copy so monthly caps still sequence correctly.
    working = json.loads(json.dumps(doc)) if args.dry_run else doc
    booked = sync_personal_dca(working, as_of=as_of, dry_run=args.dry_run)
    if not args.dry_run and booked:
        save_holdings(doc)
    print(
        json.dumps(
            {
                "as_of": as_of.isoformat(),
                "dry_run": bool(args.dry_run),
                "booked_count": len(booked),
                "booked": booked,
                "holdings_path": str(HOLDINGS_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
