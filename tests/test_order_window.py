"""Tests for A-share order-window timing (signal_date vs order_date)."""

from __future__ import annotations

import json
import sys
import tempfile
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
    "2026-07-16",  # Thu
    "2026-07-17",  # Fri
    "2026-07-20",  # Mon
    "2026-07-21",
}


class OrderWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._patcher = patch.object(cal, "trade_date_set", return_value=FAKE_DATES)
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_evening_orders_next_trading_day(self) -> None:
        timing = cal.resolve_order_window(
            "evening", as_of="2026-07-16", today="2026-07-16"
        )
        self.assertEqual(timing["signal_date"], "2026-07-16")
        self.assertEqual(timing["order_date"], "2026-07-17")
        self.assertEqual(timing["cutoff_time"], "2026-07-17 15:00 CST")
        self.assertIn("下一", timing["instruction"])

    def test_evening_friday_orders_monday(self) -> None:
        timing = cal.resolve_order_window(
            "evening", as_of="2026-07-17", today="2026-07-17"
        )
        self.assertEqual(timing["order_date"], "2026-07-20")

    def test_morning_orders_today(self) -> None:
        timing = cal.resolve_order_window(
            "morning", as_of="2026-07-16", today="2026-07-17"
        )
        self.assertEqual(timing["signal_date"], "2026-07-16")
        self.assertEqual(timing["order_date"], "2026-07-17")
        self.assertIn("今天", timing["instruction"])

    def test_qdii_note_avoids_last_night_price_claim(self) -> None:
        timing = cal.resolve_order_window("morning", as_of="2026-07-16", today="2026-07-17")
        self.assertIn("不要写成", timing["nav_note_qdii"])
        self.assertIn("基金合同", timing["nav_note_qdii"])


class EmailSlotGateTests(unittest.TestCase):
    def test_no_action_never_sends(self) -> None:
        data = {
            "has_a_action": False,
            "has_us_action": False,
            "has_buy": False,
            "has_take_profit": False,
        }
        for slot in ("morning", "evening"):
            ok, reason = email_mod.should_send_for_slot(data, slot, force=False)
            self.assertFalse(ok)
            self.assertEqual(reason, "no_trade_action")
            ok_force, reason_force = email_mod.should_send_for_slot(
                data, slot, force=True
            )
            self.assertFalse(ok_force)
            self.assertEqual(reason_force, "no_trade_action")

    def test_monthly_cap_blocks_second_dca_email(self) -> None:
        policy = load_policy()
        data = {
            "has_a_action": True,
            "has_us_action": False,
            "has_buy": True,
            "has_take_profit": False,
        }
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "trade_alert_log.json"
            log_path.write_text(
                json.dumps({"month": email_mod.month_key_cst(), "count": 1, "events": []}),
                encoding="utf-8",
            )
            with patch.object(email_mod, "ALERT_LOG_PATH", log_path):
                ok, reason = email_mod.check_monthly_email_cap(data, policy)
                self.assertFalse(ok)
                self.assertIn("monthly_cap_reached", reason)
                data2 = dict(data)
                data2["has_take_profit"] = True
                ok2, reason2 = email_mod.check_monthly_email_cap(data2, policy)
                self.assertTrue(ok2)
                self.assertIn("bypass", reason2)

        data = {
            "has_a_action": False,
            "has_us_action": True,
            "has_buy": True,
            "has_take_profit": False,
        }
        ok, reason = email_mod.should_send_for_slot(data, "evening", force=False)
        self.assertFalse(ok)
        self.assertIn("evening_no_a_share", reason)

        data["has_a_action"] = True
        ok, _ = email_mod.should_send_for_slot(data, "evening", force=False)
        self.assertTrue(ok)

    def test_morning_sends_on_a_or_us_action(self) -> None:
        data = {
            "has_a_action": True,
            "has_us_action": False,
            "has_buy": True,
            "has_take_profit": False,
        }
        ok, _ = email_mod.should_send_for_slot(data, "morning", force=False)
        self.assertTrue(ok)

    def test_build_body_includes_timing_fields(self) -> None:
        policy = load_policy()
        snapshot = {
            "as_of": "2026-07-16",
            "indexes": {
                "沪深300": {
                    "pe_ttm": 13.0,
                    "pe_percentile": 35.0,
                    "pe_percentile_1y": 40.0,
                    "verified": True,
                    "tradeable": True,
                },
                "中证500": {
                    "pe_ttm": 20.0,
                    "pe_percentile": 55.0,
                    "pe_percentile_1y": 50.0,
                    "verified": True,
                    "tradeable": True,
                },
                "标普500": {
                    "pe_ttm": 20.0,
                    "pe_percentile": 60.0,
                    "pe_percentile_1y": 50.0,
                    "verified": True,
                    "tradeable": True,
                },
                "纳斯达克100": {
                    "pe_ttm": 30.0,
                    "verified": False,
                    "tradeable": False,
                    "reference_only": True,
                },
            },
            "us_meta": {},
        }
        timing = {
            "slot": "evening",
            "signal_date": "2026-07-16",
            "order_date": "2026-07-17",
            "cutoff_time": "2026-07-17 15:00 CST",
            "instruction": "请在下一个 A 股交易日 2026-07-17 的 15:00 前申购",
            "nav_note_a_share": "A股说明",
            "nav_note_qdii": "不要写成按昨晚美股收盘价成交",
        }
        with patch.object(email_mod, "holdings_cost", return_value={}), patch.object(
            email_mod, "building_principal", return_value=10000.0
        ):
            subject, body = email_mod.build_body(
                snapshot, 300.0, policy, slot="evening", timing=timing
            )
        self.assertIn("signal_date", body)
        self.assertIn("order_date", body)
        self.assertIn("cutoff_time", body)
        self.assertIn("2026-07-17", body)
        self.assertIn("不要写成按昨晚美股收盘价成交", body)
        self.assertIn("晚间", subject)

    def test_evening_body_defers_us_buy_to_morning(self) -> None:
        policy = load_policy()
        snapshot = {
            "as_of": "2026-07-16",
            "indexes": {
                "沪深300": {
                    "pe_ttm": 10.0,
                    "pe_percentile": 25.0,
                    "pe_percentile_1y": 40.0,
                    "verified": True,
                    "tradeable": True,
                },
                "中证500": {
                    "pe_ttm": 20.0,
                    "pe_percentile": 55.0,
                    "pe_percentile_1y": 50.0,
                    "verified": True,
                    "tradeable": True,
                },
                "标普500": {
                    "pe_ttm": 18.0,
                    "pe_percentile": 40.0,
                    "pe_percentile_1y": 40.0,
                    "qdii_premium": 0.005,
                    "qdii_premium_pct": 0.5,
                    "verified": True,
                    "tradeable": True,
                },
                "纳斯达克100": {
                    "pe_ttm": 30.0,
                    "verified": False,
                    "tradeable": False,
                    "reference_only": True,
                },
            },
            "us_meta": {},
        }
        timing = {
            "slot": "evening",
            "signal_date": "2026-07-16",
            "order_date": "2026-07-17",
            "cutoff_time": "2026-07-17 15:00 CST",
            "instruction": "下一交易日操作",
            "nav_note_a_share": "A股说明",
            "nav_note_qdii": "不要写成按昨晚美股收盘价成交",
        }
        with patch.object(email_mod, "holdings_cost", return_value={}), patch.object(
            email_mod, "building_principal", return_value=10000.0
        ):
            subject, body = email_mod.build_body(
                snapshot, 300.0, policy, slot="evening", timing=timing
            )
        self.assertIn("沪深300", subject)
        self.assertNotIn("标普500", subject)
        self.assertIn("勿今晚下单", body)
        self.assertIn("等上午邮件", body)


if __name__ == "__main__":
    unittest.main()
