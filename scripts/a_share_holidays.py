"""A-share trading-day rules and the official exchange holiday table.

为什么单独一个模块
------------------
`trading_calendar.py` 依赖 akshare / pandas；当这些依赖缺失或网络不可用时，
我们仍然需要一条**不联网、有依据**的交易日判断规则，避免把法定节假日当成
交易日补记。本模块只依赖标准库，可被任何脚本安全导入。

交易日规则（权威口径）
----------------------
1. **A 股交易日**（沪深北交易所）：每周一至周五，剔除国家法定节假日及交易所
   公告的休市日。上交所 / 深交所 / 北交所每年 12 月发布下一年的休市安排，
   本模块按公告年份内置。
2. **场外基金扣款 / 申购**：以基金「开放日」为准，公募基金开放日通常等同
   A 股交易日。定投扣款日若逢非交易日（周末、法定节假日）则**顺延至下一个
   开放日**；15:00 前提交按当日净值确认，15:00 后顺延到下一开放日。
   （博时基金直销定投业务规则：「按日定投：系统会在周一至周五的每个交易日
   发起定投扣款。」）
3. **QDII（如南方纳斯达克100 016452）**：场外 QDII 的申购申请截止仍按
   **A 股交易日 15:00**，但最终确认净值取决于境外市场估值日；境外市场休市
   （如美股假日）时，基金公司可能暂停当日申购或把估值顺延到下一境外交易日。
   因此扣款日基准 = A 股交易日，确认净值可能再顺延，两者要分开表述。
4. 数据源优先级：AKShare `tool_trade_date_hist_sina`（新浪财经交易日历，
   含交易所临时调整）→ 本模块内置的官方休市表 → 无法判断时**跳过该日**
   （fail-closed）。跳过的交易日不会丢失：下一次运行仍会从上一笔之后逐日重算。

来源
----
上交所 / 深交所 / 北交所 2026 年部分节假日休市安排（2025-12-22 公告）：
元旦 1/1–1/3、春节 2/15–2/23、清明 4/4–4/6、劳动节 5/1–5/5、
端午 6/19–6/21、中秋 9/25–9/27、国庆 10/1–10/7。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache

#: 各年度官方休市区间（含首尾），格式 (起始日, 结束日)。
A_SHARE_HOLIDAY_RANGES: dict[int, tuple[tuple[str, str], ...]] = {
    2026: (
        ("2026-01-01", "2026-01-03"),  # 元旦
        ("2026-02-15", "2026-02-23"),  # 春节
        ("2026-04-04", "2026-04-06"),  # 清明节
        ("2026-05-01", "2026-05-05"),  # 劳动节
        ("2026-06-19", "2026-06-21"),  # 端午节
        ("2026-09-25", "2026-09-27"),  # 中秋节
        ("2026-10-01", "2026-10-07"),  # 国庆节
    ),
}

#: 交易日规则说明，按 `market` 键索引（与 portfolio_policy.json 的 schedules 对应）。
TRADING_DAY_RULES: dict[str, dict[str, str]] = {
    "a_share": {
        "label": "A 股交易日",
        "rule": (
            "沪深北交易所交易日：周一至周五，剔除法定节假日与交易所公告休市日；"
            "非交易日不扣款，顺延至下一交易日"
        ),
        "source": "上交所/深交所/北交所年度休市安排 + AKShare 新浪交易日历",
    },
    "qdii_us": {
        "label": "A 股交易日（QDII 扣款基准）",
        "rule": (
            "扣款/申购申请以 A 股交易日为基准、15:00 截止；境外市场休市时"
            "基金公司可能暂停申购或把确认净值顺延到下一境外交易日"
        ),
        "source": "基金合同开放日 + 上交所/深交所年度休市安排",
    },
}

DEFAULT_MARKET = "a_share"


def parse_day(day: date | str | None = None) -> date:
    if day is None:
        return date.today()
    if isinstance(day, str):
        return datetime.strptime(day[:10], "%Y-%m-%d").date()
    return day


@lru_cache(maxsize=16)
def a_share_holiday_set(year: int) -> frozenset[str]:
    """All ISO dates inside the official holiday ranges for ``year``."""
    out: set[str] = set()
    for start_s, end_s in A_SHARE_HOLIDAY_RANGES.get(int(year), ()):
        cur = datetime.strptime(start_s, "%Y-%m-%d").date()
        end = datetime.strptime(end_s, "%Y-%m-%d").date()
        while cur <= end:
            out.add(cur.isoformat())
            cur += timedelta(days=1)
    return frozenset(out)


def offline_a_share_trading_day(day: date | str | None = None) -> bool | None:
    """Holiday-aware offline verdict.

    Returns ``None`` when we hold no holiday table for that year — callers must
    treat that as "unknown" and fail closed rather than assume Mon–Fri.
    """
    d = parse_day(day)
    if d.year not in A_SHARE_HOLIDAY_RANGES:
        return None
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in a_share_holiday_set(d.year)


def trading_day_rule(market: str | None = None) -> dict[str, str]:
    """Rule text for a schedule ``market``.

    Unknown markets fall back to the A-share rule **and report the effective
    market**, so an audit of the returned dict can never mistake a typo for a
    real market.
    """
    key = str(market or DEFAULT_MARKET).strip().lower() or DEFAULT_MARKET
    if key not in TRADING_DAY_RULES:
        key = DEFAULT_MARKET
    return {"market": key, **TRADING_DAY_RULES[key]}


def trading_day_rule_text(market: str | None = None) -> str:
    rule = trading_day_rule(market)
    return f"{rule['label']}：{rule['rule']}（来源：{rule['source']}）"


def is_trading_day_for(market: str | None, day: date | str | None = None) -> bool:
    """Resolve a schedule `market` to a trading-day verdict (A-share calendar).

    Both ``a_share`` and ``qdii_us`` use the A-share calendar for the deduction
    day; QDII additionally depends on the overseas market for NAV confirmation,
    which is reported separately and never silently merged into this verdict.
    """
    verdict = offline_a_share_trading_day(day)
    if verdict is None:
        # No table for that year: never invent a trading day.
        return False
    return bool(verdict)


def describe(year: int | None = None) -> str:
    """Human-readable summary used by docs / CLI output."""
    years = [int(year)] if year else sorted(A_SHARE_HOLIDAY_RANGES)
    parts = []
    for y in years:
        ranges = A_SHARE_HOLIDAY_RANGES.get(y)
        if not ranges:
            parts.append(f"{y}: 无内置休市表（需联网交易日历）")
            continue
        bits = "、".join(f"{s}~{e}" for s, e in ranges)
        parts.append(f"{y}: {bits}")
    return "\n".join(parts)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="A 股交易日规则与官方休市表")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD，默认今天")
    parser.add_argument("--market", default=DEFAULT_MARKET, help="a_share / qdii_us")
    args = parser.parse_args()
    day = parse_day(args.date)
    print(trading_day_rule_text(args.market))
    print(f"{day.isoformat()} → {'交易日' if is_trading_day_for(args.market, day) else '非交易日'}")
    print("")
    print(describe())


if __name__ == "__main__":
    main()
