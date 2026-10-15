"""Tests for the A-share trading-day rules and the official holiday table.

规则来源：上交所 / 深交所 / 北交所 2026 年部分节假日休市安排（2025-12-22）。
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import sync_personal_dca  # noqa: E402
from a_share_holidays import (  # noqa: E402
    A_SHARE_HOLIDAY_RANGES,
    a_share_holiday_set,
    is_trading_day_for,
    offline_a_share_trading_day,
    trading_day_rule,
    trading_day_rule_text,
)


class AShareHolidayTests(unittest.TestCase):
    def test_2026_official_ranges_are_present(self) -> None:
        ranges = A_SHARE_HOLIDAY_RANGES[2026]
        self.assertIn(("2026-01-01", "2026-01-03"), ranges)  # 元旦
        self.assertIn(("2026-02-15", "2026-02-23"), ranges)  # 春节
        self.assertIn(("2026-10-01", "2026-10-07"), ranges)  # 国庆
        self.assertEqual(len(ranges), 7)

    def test_weekend_is_not_a_trading_day(self) -> None:
        self.assertFalse(offline_a_share_trading_day(date(2026, 10, 17)))  # 周六
        self.assertFalse(offline_a_share_trading_day(date(2026, 10, 18)))  # 周日

    def test_regular_weekdays_are_trading_days(self) -> None:
        self.assertTrue(offline_a_share_trading_day(date(2026, 10, 15)))  # 周四
        self.assertTrue(offline_a_share_trading_day(date(2026, 10, 16)))  # 周五
        self.assertTrue(is_trading_day_for("a_share", date(2026, 10, 8)))

    def test_national_holiday_is_not_a_trading_day(self) -> None:
        for day in (date(2026, 10, 1), date(2026, 10, 6), date(2026, 10, 7)):
            self.assertFalse(offline_a_share_trading_day(day), msg=day.isoformat())

    def test_spring_festival_block(self) -> None:
        self.assertFalse(offline_a_share_trading_day(date(2026, 2, 16)))
        self.assertTrue(offline_a_share_trading_day(date(2026, 2, 24)))

    def test_unknown_year_fails_closed(self) -> None:
        self.assertIsNone(offline_a_share_trading_day(date(2030, 6, 3)))
        self.assertFalse(is_trading_day_for("a_share", date(2030, 6, 3)))

    def test_holiday_set_expands_ranges(self) -> None:
        days = a_share_holiday_set(2026)
        self.assertIn("2026-10-01", days)
        self.assertIn("2026-10-07", days)
        self.assertNotIn("2026-10-08", days)
        self.assertGreater(len(days), 20)

    def test_market_rule_text(self) -> None:
        self.assertIn("A 股交易日", trading_day_rule("a_share")["label"])
        qdii = trading_day_rule("qdii_us")
        self.assertIn("A 股交易日", qdii["label"])
        self.assertIn("境外", qdii["rule"])
        # 未知 market 回退到 A 股口径，不静默放行
        self.assertEqual(trading_day_rule("does_not_exist")["market"], "a_share")
        self.assertIn("沪深北交易所", trading_day_rule_text("a_share"))


class TradingCalendarFallbackTests(unittest.TestCase):
    """AKShare 不可用时必须回退到官方休市表，而不是「周一至周五」。"""

    def test_default_is_trading_day_uses_holiday_table(self) -> None:
        # sys.modules[name] = None makes `import name` raise ImportError.
        with unittest.mock.patch.dict(sys.modules, {"trading_calendar": None}):
            self.assertFalse(sync_personal_dca.default_is_trading_day(date(2026, 10, 1)))
            self.assertFalse(sync_personal_dca.default_is_trading_day(date(2026, 10, 17)))
            self.assertTrue(sync_personal_dca.default_is_trading_day(date(2026, 10, 15)))

    def test_default_is_trading_day_fails_closed_without_a_table(self) -> None:
        with unittest.mock.patch.dict(sys.modules, {"trading_calendar": None}):
            self.assertFalse(sync_personal_dca.default_is_trading_day(date(2030, 6, 3)))


if __name__ == "__main__":
    unittest.main()
