"""A-share trading calendar helpers (excludes weekends and official holidays)."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import akshare as ak
import pandas as pd

CST = timezone(timedelta(hours=8))
DEFAULT_RETRIES = 3
RETRY_BASE_SECONDS = 2


@lru_cache(maxsize=1)
def trade_date_set() -> set[str]:
    frame = ak.tool_trade_date_hist_sina()
    if frame is None or getattr(frame, "empty", True):
        raise RuntimeError("AKShare 交易日历返回空表")
    if "trade_date" not in frame.columns:
        raise RuntimeError(
            f"AKShare 交易日历字段变更：缺少 trade_date，实际列={list(frame.columns)}"
        )
    series = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    dates = set(series.tolist())
    if len(dates) < 1000:
        raise RuntimeError(f"AKShare 交易日历样本过少（{len(dates)}），疑似接口异常")
    return dates


def today_cst() -> date:
    return datetime.now(CST).date()


def is_a_share_trading_day(day: date | str | None = None) -> bool:
    if day is None:
        day = today_cst()
    elif isinstance(day, str):
        day = datetime.strptime(day[:10], "%Y-%m-%d").date()
    return day.isoformat() in trade_date_set()


def _write_github_summary(markdown: str) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary:
        return
    with open(summary, "a", encoding="utf-8") as handle:
        handle.write(markdown)
        if not markdown.endswith("\n"):
            handle.write("\n")


def resolve_trading_day(
    day: str,
    *,
    retries: int = DEFAULT_RETRIES,
) -> bool:
    """Fetch calendar with retries. Raises on persistent failure."""
    attempts = max(1, retries)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            trade_date_set.cache_clear()
            return is_a_share_trading_day(day)
        except Exception as exc:
            last_error = exc
            print(
                f"交易日判断失败（第 {attempt}/{attempts} 次）: {exc}",
                file=sys.stderr,
            )
            if attempt < attempts:
                time.sleep(RETRY_BASE_SECONDS * attempt)
    raise RuntimeError(f"交易日判断失败（已重试 {attempts} 次）: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(description="检查是否为 A 股交易日")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD，默认今天(CST)")
    parser.add_argument(
        "--github-output",
        action="store_true",
        help="写出 run=yes/no 到 GITHUB_OUTPUT",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"网络失败重试次数（默认 {DEFAULT_RETRIES}）",
    )
    args = parser.parse_args()
    day = args.date or today_cst().isoformat()
    try:
        ok = resolve_trading_day(day, retries=args.retries)
    except Exception as exc:
        message = str(exc)
        print(message, file=sys.stderr)
        _write_github_summary(
            "### 交易日判断失败\n\n"
            f"- 日期: `{day}`\n"
            f"- 错误: `{message}`\n"
            "- 工作流已中断，**不是**「今天无信号」。"
            "请检查 AKShare/新浪交易日历网络后重跑。\n"
        )
        raise SystemExit(1) from exc

    print(f"{day} {'是' if ok else '不是'} A股交易日")
    _write_github_summary(
        "### 交易日判断\n\n"
        f"- 日期: `{day}`\n"
        f"- A股交易日: `{'yes' if ok else 'no'}`\n"
    )
    if args.github_output:
        path = Path(os.environ.get("GITHUB_OUTPUT", ""))
        line = f"run={'yes' if ok else 'no'}\n"
        if path.name:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        else:
            print(line, end="")


if __name__ == "__main__":
    main()
