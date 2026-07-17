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


def parse_day(day: date | str | None = None) -> date:
    if day is None:
        return today_cst()
    if isinstance(day, str):
        return datetime.strptime(day[:10], "%Y-%m-%d").date()
    return day


def next_a_share_trading_day(day: date | str | None = None) -> date:
    """First A-share trading day strictly after `day` (default: today CST)."""
    start = parse_day(day)
    dates = trade_date_set()
    for offset in range(1, 60):
        candidate = start + timedelta(days=offset)
        if candidate.isoformat() in dates:
            return candidate
    raise RuntimeError(f"未能在 {start} 之后 60 天内找到下一个 A 股交易日")


def previous_a_share_trading_day(day: date | str | None = None) -> date:
    """Latest A-share trading day on or before `day` (default: today CST)."""
    start = parse_day(day)
    dates = trade_date_set()
    for offset in range(0, 60):
        candidate = start - timedelta(days=offset)
        if candidate.isoformat() in dates:
            return candidate
    raise RuntimeError(f"未能在 {start} 之前 60 天内找到 A 股交易日")


def resolve_order_window(
    slot: str,
    *,
    as_of: date | str | None = None,
    today: date | str | None = None,
) -> dict[str, str]:
    """Map email slot to signal_date / order_date / cutoff_time (场外申购语义).

    evening: valuation after A-share close → order on *next* trading day before 15:00
    morning: remind to order *today* before 15:00 (A-share + QDII application cutoff)
    """
    slot_norm = (slot or "morning").strip().lower()
    today_d = parse_day(today)
    as_of_d = parse_day(as_of) if as_of is not None else today_d

    if slot_norm == "evening":
        # Signal is today's close (or as_of); subscribe next open trading day.
        signal = as_of_d
        if not is_a_share_trading_day(signal):
            signal = previous_a_share_trading_day(signal)
        order = next_a_share_trading_day(signal)
        instruction = (
            f"请在下一个 A 股交易日 {order.isoformat()} 的 15:00 前在银行 APP 提交申购/赎回"
        )
    else:
        # Morning: order today (must be a trading day for the workflow to email).
        signal = as_of_d
        if not is_a_share_trading_day(signal):
            signal = previous_a_share_trading_day(signal)
        if is_a_share_trading_day(today_d):
            order = today_d
        else:
            order = next_a_share_trading_day(today_d)
        instruction = (
            f"请在今天（A 股交易日 {order.isoformat()}）15:00 前在银行 APP 提交申购/赎回"
            if order == today_d
            else f"今天非 A 股交易日；请在下一交易日 {order.isoformat()} 15:00 前提交"
        )

    return {
        "slot": slot_norm,
        "signal_date": signal.isoformat(),
        "order_date": order.isoformat(),
        "cutoff_time": f"{order.isoformat()} 15:00 CST",
        "instruction": instruction,
        "nav_note_a_share": (
            "场外 A 股指数基金：15:00 前提交按该申请日收盘净值确认；"
            "15:00 后/非交易日提交顺延下一开放日。下单时看到的净值通常是上一交易日旧净值。"
        ),
        "nav_note_qdii": (
            "场外 QDII：申请截止仍以 A 股交易日 15:00 为准；"
            "最终成交净值以基金合同估值日为准，"
            "不要写成「按昨晚美股收盘价成交」。境外休市/节假日可能顺延。"
        ),
    }


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
