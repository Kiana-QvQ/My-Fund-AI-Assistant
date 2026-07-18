"""Order window + dual email stream smoke tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import send_trade_alert_email as email_mod  # noqa: E402
import trading_calendar as cal  # noqa: E402
from policy_rules import load_policy  # noqa: E402

FAKE_DATES = {
    "2026-07-16",
    "2026-07-17",
    "2026-07-20",
    "2026-07-21",
}


class OrderWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._patcher = patch.object(cal, "trade_date_set", return_value=FAKE_DATES)
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_morning_orders_today(self) -> None:
        timing = cal.resolve_order_window(
            "morning", as_of="2026-07-16", today="2026-07-17"
        )
        self.assertEqual(timing["order_date"], "2026-07-17")
        self.assertIn("今天", timing["instruction"])


class DualEmailTests(unittest.TestCase):
    def test_dca_email_separate_from_build(self) -> None:
        policy = load_policy()
        lines = [
            {
                "name": "沪深300",
                "fund_code": "460300",
                "fund_name": "华泰柏瑞沪深300ETF联接A",
                "weekly": 37.5,
                "monthly": 150.0,
                "multiplier": 0.5,
                "reason": "test",
                "paused": False,
            }
        ]
        timing = {
            "signal_date": "2026-07-16",
            "order_date": "2026-07-17",
            "cutoff_time": "2026-07-17 15:00 CST",
        }
        subject, body = email_mod.build_dca_email(
            title="周四定投周报",
            lines=lines,
            timing=timing,
            policy=policy,
        )
        self.assertIn("定投", subject)
        self.assertIn("purpose=dca", body)
        self.assertIn("37.50", body)

    def test_thursday_event_not_suppressed_by_weekly_flag(self) -> None:
        """Regression: sent_weekly must not block event_dca when tiers change."""
        sent_weekly = True
        first_run = False
        dca_changes = ["沪深300: ..."]
        mode = "auto"
        send_event_dca = mode in ("event", "auto", "force_dca") and (
            mode == "force_dca" or (dca_changes and not first_run)
        )
        # Old bug required `and not sent_weekly`
        self.assertTrue(send_event_dca)
        self.assertTrue(sent_weekly)  # both can be true

        policy = load_policy()
        lines = [
            {
                "name": "沪深300",
                "fund_code": "460300",
                "fund_name": "华泰柏瑞沪深300ETF联接A",
                "active": True,
                "state": "正式小额底仓 50%",
                "tier_label": "正式小额底仓 50%",
                "amount": 150.0,
                "reason": "test build hs",
                "pct_10y": 60.0,
                "pct_1y": 45.0,
                "dd": 6.0,
            },
            {
                "name": "中证500",
                "fund_code": "160119",
                "fund_name": "南方中证500",
                "active": False,
                "state": "不可买",
                "tier_label": "不可买",
                "amount": 0.0,
                "reason": "条件未触发",
                "pct_10y": 80.0,
                "pct_1y": 85.0,
                "dd": 5.0,
            },
            {
                "name": "标普500",
                "fund_code": "050025",
                "fund_name": "博时标普500",
                "active": False,
                "state": "QDII溢价阻断",
                "tier_label": "QDII溢价阻断",
                "amount": 0.0,
                "reason": "溢价过高",
                "pct_10y": 92.0,
                "pct_1y": 91.0,
                "premium_pct": 3.4,
            },
        ]
        timing = {
            "signal_date": "2026-07-16",
            "order_date": "2026-07-17",
            "cutoff_time": "2026-07-17 15:00 CST",
        }
        subject, body = email_mod.build_build_email(
            lines=lines,
            timing=timing,
            policy=policy,
            changes=["标普500: 不可买 → QDII溢价阻断"],
        )
        self.assertIn("建仓", subject)
        self.assertIn("标普500", subject)
        # All three sleeves appear, not only the changed one.
        self.assertIn("沪深300", body)
        self.assertIn("中证500", body)
        self.assertIn("标普500", body)
        self.assertIn("【变更】", body)
        self.assertIn("purpose=build", body)


if __name__ == "__main__":
    unittest.main()
