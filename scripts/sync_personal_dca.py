"""Auto-ledger personal trading-day DCA schedules into portfolio_holdings.json.

Policy `dca.personal_schedules` describes broker auto-invest rules. Since
2026-10-15 all four held funds run on 招商银行 App "每交易日" auto-invest:
短债/沪深300/中证500 各 ¥5 与 纳指100 ¥10, with **no monthly stop amount**.
This script mirrors those installs into the advice ledger so README / GitHub
stay current without manual daily `record_holding.py` calls.

Behavior:
- Only enabled schedules with trading_day cadence
- `effective_from` (per schedule) is never crossed: dates before it are not
  backfilled, so a newly created schedule cannot invent history
- If the fund already has at least one purpose=dca buy: backfill every missing
  trading day from the day after the last personal DCA through --as-of
- If no personal DCA history yet: only book --as-of (avoid inventing a month)
- `monthly_stop_amount` absent / null / 0 = unlimited: book every trading day
- Idempotent per date+fund+amount+note; `--since` (with `--fund`) overrides
  `effective_from` for a deliberate, human-initiated reconciliation
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

from fund_nav import (  # noqa: E402
    ensure_nav_range,
    estimate_shares,
    fee_percent_from_snapshot,
    lookup_nav_on_or_before,
)
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

SNAPSHOT_PATH = ROOT / "data" / "market_snapshot.json"


IsTradingDay = Callable[[date], bool]


def _load_snapshot() -> dict:
    if not SNAPSHOT_PATH.is_file():
        return {}
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def resolve_nav_for_buy(
    fund_code: str,
    day: date,
    *,
    snapshot: dict | None = None,
) -> tuple[float | None, str | None]:
    """Best-effort NAV for auto-ledger: history cache, else snapshot."""
    try:
        series = ensure_nav_range(fund_code, start=day, end=day)
        hit = lookup_nav_on_or_before(series, day)
        if hit is not None:
            return float(hit[1]), hit[0]
    except Exception as exc:  # noqa: BLE001
        print(f"警告: {fund_code} {day} 历史净值不可用（{exc}）", file=sys.stderr)
    snap = snapshot if snapshot is not None else _load_snapshot()
    fund = (snap.get("funds") or {}).get(str(fund_code)) or {}
    nav = fund.get("nav")
    if isinstance(nav, (int, float)) and float(nav) > 0:
        return float(nav), str(fund.get("nav_date") or day.isoformat())[:10]
    return None, None


def default_is_trading_day(day: date) -> bool:
    """Prefer AKShare A-share calendar; fall back to the official holiday table.

    Both `a_share` and `qdii_us` schedules deduct on A-share trading days, so one
    calendar function is enough. The previous Mon–Fri fallback silently treated
    national holidays as trading days; the built-in exchange table does not.
    """
    try:
        from trading_calendar import is_a_share_trading_day

        return bool(is_a_share_trading_day(day))
    except Exception as exc:  # noqa: BLE001 — local env / numpy drift
        from a_share_holidays import offline_a_share_trading_day

        offline = offline_a_share_trading_day(day)
        # Import failures often dump a long traceback before raising; keep it short.
        msg = f"{type(exc).__name__}: {exc}"
        if offline is None:
            print(
                f"警告: 交易日历不可用（{msg}），且无 {day.year} 年官方休市表；"
                f"{day.isoformat()} 按非交易日跳过（fail-closed，下次运行会重算）",
                file=sys.stderr,
            )
            return False
        print(
            f"警告: 交易日历不可用（{msg}），"
            f"按 {day.year} 年官方休市表回退判定 {day.isoformat()}",
            file=sys.stderr,
        )
        return bool(offline)


def personal_dca_note(schedule: dict, fund_code: str) -> str:
    name = str(schedule.get("name") or fund_code).strip()
    return f"{name}交易日定投"


def parse_effective_from(schedule: dict) -> date | None:
    """First date this schedule may be auto-ledgered (None = no constraint).

    ``effective_from`` wins when present. Otherwise ``reconciled_through`` — the
    last date already reconciled by hand against the broker app — is used, with
    auto-ledgering starting the day after it. Keeping both means the boundary is
    explicit and cannot drift silently.
    """
    code = schedule.get("fund_code") or "?"

    def _parse(raw_value: object) -> date | None:
        raw = str(raw_value or "").strip()[:10]
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            return None

    explicit = _parse(schedule.get("effective_from"))
    if explicit is not None:
        return explicit
    if str(schedule.get("effective_from") or "").strip():
        print(
            f"警告: {code} 的 effective_from 格式无效"
            f"（{schedule.get('effective_from')}），已忽略该约束",
            file=sys.stderr,
        )
        return None

    through_raw = str(schedule.get("reconciled_through") or "").strip()
    through = _parse(through_raw)
    if through is not None:
        return through + timedelta(days=1)
    if through_raw:
        print(
            f"警告: {code} 的 reconciled_through 格式无效（{through_raw}），已忽略",
            file=sys.stderr,
        )
    return None


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


def personal_dca_month_entries(
    doc: dict,
    fund_code: str,
    month_key: str,
) -> int:
    """How many installments this fund already booked in the month."""
    count = 0
    for tx in doc.get("transactions") or []:
        if not is_personal_dca_tx(tx, fund_code):
            continue
        if str(tx.get("trade_date") or "")[:7] != month_key:
            continue
        count += 1
    return count


def collect_personal_dca(
    snapshot: dict | None = None,
    policy: dict | None = None,
    *,
    today: date | None = None,
    holdings_doc: dict | None = None,
) -> list[dict]:
    """Monitoring rows for the bank's daily auto-invest (one per schedule).

    Shared by the alert email and the README dashboard so both always tell the
    same story. Carries no order amount: 招商银行 already debits every trading
    day, so the only actionable output is the 维持 / 建议暂停 / 可考虑加码 verdict.
    """
    from investment_plan import (  # noqa: WPS433
        personal_dca_monitor,
        personal_dca_schedules,
        personal_dca_verdict_rank,
    )

    as_of = today or today_cst()
    policy = policy or load_policy()
    snap = snapshot if snapshot is not None else _load_snapshot()
    doc = holdings_doc if holdings_doc is not None else load_holdings()
    indexes = (snap or {}).get("indexes") or {}
    month_key = as_of.strftime("%Y-%m")
    trading_today = bool(default_is_trading_day(as_of))

    rows: list[dict] = []
    for fund_code, schedule in personal_dca_schedules(policy).items():
        index_name = schedule.get("index")
        item: dict = {}
        if index_name:
            item = indexes.get(index_name) or {}
        spent = personal_dca_month_spent(doc, fund_code, month_key)
        line = personal_dca_monitor(
            fund_code,
            percentile_10y=item.get("pe_percentile"),
            premium=item.get("qdii_premium"),
            drawdown_from_52w_high=item.get("drawdown_from_52w_high"),
            is_trading_day=trading_today,
            month_spent=spent,
            policy=policy,
        )
        line["pct_10y"] = item.get("pe_percentile")
        premium_pct = item.get("qdii_premium_pct")
        if premium_pct is None and isinstance(item.get("qdii_premium"), (int, float)):
            premium_pct = float(item["qdii_premium"]) * 100
        line["premium_pct"] = premium_pct
        line["month_entries"] = personal_dca_month_entries(doc, fund_code, month_key)
        rows.append(line)

    rows.sort(key=lambda row: personal_dca_verdict_rank(row.get("verdict")))
    return rows


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
    snapshot: dict | None = None,
    since: date | None = None,
    fund_codes: set[str] | None = None,
) -> list[dict]:
    """Book missing personal DCA installs into ``doc``; return booked rows.

    ``since`` overrides every schedule's ``effective_from`` (deliberate,
    human-initiated backfill). ``fund_codes`` restricts the run to a subset.
    """
    as_of = as_of or today_cst()
    policy = policy or load_policy()
    check = is_trading_day or default_is_trading_day
    snap = snapshot if snapshot is not None else _load_snapshot()
    booked: list[dict] = []

    for fund_code, schedule in personal_dca_schedules(policy).items():
        if fund_codes is not None and str(fund_code) not in fund_codes:
            continue
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
        floor = since or parse_effective_from(schedule)
        last = last_personal_dca_date(doc, fund_code)
        if last is None:
            start = as_of
        else:
            start = last + timedelta(days=1)
        if floor is not None and start < floor:
            start = floor
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
            nav, nav_date = resolve_nav_for_buy(fund_code, day, snapshot=snap)
            fee = fee_percent_from_snapshot(snap, fund_code)
            shares = None
            if nav is not None:
                shares = estimate_shares(amount, nav, fee_percent=fee)
            entry = {
                "fund_code": fund_code,
                "trade_date": day.isoformat(),
                "amount": amount,
                "note": note,
                "purpose": "dca",
                "reason": line.get("reason"),
                "market": schedule.get("market") or "a_share",
                "broker": schedule.get("broker"),
                "nav": nav,
                "nav_date": nav_date,
                "shares": shares,
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
                        "nav": nav,
                        "shares": shares,
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
                nav=nav,
                shares=shares,
            )
            booked.append(entry)

    return booked


def main() -> None:
    parser = argparse.ArgumentParser(
        description="按 personal_schedules 自动补记每交易日定投到账本（四只标的，幂等）"
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
    parser.add_argument(
        "--since",
        default=None,
        help=(
            "覆盖政策里的 effective_from，从该日期开始补记（YYYY-MM-DD）；"
            "用于人工确认过的历史补账，建议配合 --fund"
        ),
    )
    parser.add_argument(
        "--fund",
        action="append",
        default=None,
        help="只处理指定基金代码（可重复）；例如 --fund 016452",
    )
    args = parser.parse_args()
    as_of = parse_trade_date(args.as_of) if args.as_of else today_cst()
    since = parse_trade_date(args.since) if args.since else None
    fund_codes = {str(code) for code in args.fund} if args.fund else None
    doc = load_holdings()
    # Dry-run mutates a copy so caps/sequencing still behave the same.
    working = json.loads(json.dumps(doc)) if args.dry_run else doc
    booked = sync_personal_dca(
        working,
        as_of=as_of,
        dry_run=args.dry_run,
        since=since,
        fund_codes=fund_codes,
    )
    if not args.dry_run and booked:
        # apply_buy only appends transactions, so keep the ledger's as_of honest.
        doc["as_of"] = max(str(row["trade_date"]) for row in booked)
        save_holdings(doc)
    print(
        json.dumps(
            {
                "as_of": as_of.isoformat(),
                "since": since.isoformat() if since else None,
                "funds": sorted(fund_codes) if fund_codes else None,
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
