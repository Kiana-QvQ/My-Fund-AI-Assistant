"""A-share trading calendar helpers (excludes weekends and official holidays)."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import akshare as ak
import pandas as pd

CST = timezone(timedelta(hours=8))


@lru_cache(maxsize=1)
def trade_date_set() -> set[str]:
    frame = ak.tool_trade_date_hist_sina()
    series = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    return set(series.tolist())


def today_cst() -> date:
    return datetime.now(CST).date()


def is_a_share_trading_day(day: date | str | None = None) -> bool:
    if day is None:
        day = today_cst()
    elif isinstance(day, str):
        day = datetime.strptime(day[:10], "%Y-%m-%d").date()
    return day.isoformat() in trade_date_set()


def main() -> None:
    parser = argparse.ArgumentParser(description="检查是否为 A 股交易日")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD，默认今天(CST)")
    parser.add_argument(
        "--github-output",
        action="store_true",
        help="写出 run=yes/no 到 GITHUB_OUTPUT",
    )
    args = parser.parse_args()
    day = args.date or today_cst().isoformat()
    ok = is_a_share_trading_day(day)
    print(f"{day} {'是' if ok else '不是'} A股交易日")
    if args.github_output:
        import os
        from pathlib import Path

        path = Path(os.environ.get("GITHUB_OUTPUT", ""))
        line = f"run={'yes' if ok else 'no'}\n"
        if path.name:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        else:
            print(line, end="")


if __name__ == "__main__":
    main()
