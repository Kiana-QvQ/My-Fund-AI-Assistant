"""DCA budget helpers (portfolio cap / remaining Thursdays)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import send_trade_alert_email as email_mod  # noqa: E402
from investment_plan import (  # noqa: E402
    allocate_dca_plan,
    dca_amounts,
    personal_dca_line,
    personal_dca_monitor,
    personal_dca_schedules,
    resolve_dca_line,
)
from policy_rules import load_policy  # noqa: E402


def policy_with_weekly_budget(**dca_overrides) -> dict:
    """Opt back into the legacy portfolio weekly budget for budget-maths tests."""
    policy = load_policy()
    dca = dict(policy.get("dca") or {})
    dca["portfolio_weekly_enabled"] = True
    dca.update(dca_overrides)
    policy["dca"] = dca
    return policy


class DcaBudgetTests(unittest.TestCase):
    def test_sleeve_helper_uses_weight(self) -> None:
        policy = load_policy()
        monthly, weekly = dca_amounts(1.0, policy=policy, weight=0.27)
        self.assertEqual(monthly, 81.0)
        self.assertEqual(weekly, 20.25)

    def test_paused_equity_flows_to_short_bond(self) -> None:
        policy = policy_with_weekly_budget()
        equity = [
            resolve_dca_line("沪深300", 95.0, policy=policy),
            resolve_dca_line("中证500", 95.0, policy=policy),
            resolve_dca_line(
                "标普500",
                40.0,
                premium=0.05,
                verified=True,
                tradeable=True,
                policy=policy,
            ),
            resolve_dca_line("纳斯达克100", 10.0, policy=policy),
        ]
        lines = allocate_dca_plan(
            equity, policy=policy, today=date(2026, 6, 4), month_spent=0
        )
        by_name = {ln["name"]: ln for ln in lines}
        self.assertEqual(by_name["沪深300"]["monthly"], 0.0)
        self.assertEqual(by_name["中证500"]["monthly"], 0.0)
        self.assertEqual(by_name["标普500"]["monthly"], 0.0)
        self.assertEqual(by_name["短债"]["monthly"], 300.0)


class DcaSpentLedgerTests(unittest.TestCase):
    def test_only_dca_purpose_counts(self) -> None:
        month = "2026-07"
        doc = {
            "transactions": [
                {
                    "side": "buy",
                    "fund_code": "460300",
                    "trade_date": "2026-07-10",
                    "amount": 40.5,
                    "purpose": "dca",
                    "note": "定投",
                },
                {
                    "side": "buy",
                    "fund_code": "012773",
                    "trade_date": "2026-07-10",
                    "amount": 2000.0,
                    "purpose": "bootstrap",
                    "note": "短债建仓（非定投）",
                },
                {
                    "side": "buy",
                    "fund_code": "160119",
                    "trade_date": "2026-07-11",
                    "amount": 16.5,
                    "purpose": "bootstrap",
                    "note": "宽松观测仓",
                },
                {
                    "side": "buy",
                    "fund_code": "460300",
                    "trade_date": "2026-07-12",
                    "amount": 37.5,
                    "purpose": "",
                    "note": "周度定投沪深300",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "holdings.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            with patch.object(email_mod, "HOLDINGS_PATH", path):
                spent = email_mod.actual_dca_spent(month)
        self.assertEqual(spent, 78.0)  # 40.5 + 37.5；建仓/非定投不计

    def test_personal_dca_can_be_excluded_from_portfolio_spent(self) -> None:
        month = "2026-08"
        doc = {
            "transactions": [
                {
                    "side": "buy",
                    "fund_code": "016452",
                    "trade_date": "2026-08-07",
                    "amount": 10.0,
                    "purpose": "dca",
                },
                {
                    "side": "buy",
                    "fund_code": "460300",
                    "trade_date": "2026-08-07",
                    "amount": 20.0,
                    "purpose": "dca",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "holdings.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            with patch.object(email_mod, "HOLDINGS_PATH", path):
                spent = email_mod.actual_dca_spent(
                    month,
                    load_policy(),
                    fund_codes={"460300"},
                )
        self.assertEqual(spent, 20.0)


class PersonalDcaScheduleTests(unittest.TestCase):
    """四只标的自 2026-10-15 起改为每交易日自动定投，且不设月终止金额。"""

    def test_all_four_schedules_are_unlimited_trading_day(self) -> None:
        policy = load_policy()
        schedules = personal_dca_schedules(policy)
        self.assertEqual(set(schedules), {"012773", "460300", "160119", "016452"})
        expected = {"012773": 5.0, "460300": 5.0, "160119": 5.0, "016452": 10.0}
        for code, amount in expected.items():
            line = personal_dca_line(
                code,
                month_spent=100000.0,  # 累积金额再大也不该停
                is_trading_day=True,
                policy=policy,
            )
            self.assertEqual(line["daily_amount"], amount)
            self.assertIsNone(line["monthly_stop_amount"])
            self.assertIsNone(line["month_remaining"])
            self.assertTrue(line["unlimited_monthly"])
            self.assertEqual(line["today_amount"], amount)
            self.assertTrue(line["executable"])
            self.assertIn("未设月终止金额", line["reason"])

    def test_monthly_stop_still_supported_when_configured(self) -> None:
        policy = policy_with_weekly_budget()
        policy["dca"]["personal_schedules"]["016452"]["monthly_stop_amount"] = 215
        line = personal_dca_line(
            "016452", month_spent=205.0, is_trading_day=True, policy=policy
        )
        self.assertEqual(line["today_amount"], 10.0)
        self.assertEqual(line["month_remaining"], 10.0)
        self.assertFalse(line["unlimited_monthly"])
        self.assertTrue(line["executable"])

        stopped = personal_dca_line(
            "016452", month_spent=215.0, is_trading_day=True, policy=policy
        )
        self.assertEqual(stopped["today_amount"], 0.0)
        self.assertIn("终止金额", stopped["reason"])

    def test_zero_or_null_stop_means_unlimited(self) -> None:
        policy = load_policy()
        for raw in (None, 0, "0"):
            policy["dca"]["personal_schedules"]["460300"]["monthly_stop_amount"] = raw
            line = personal_dca_line(
                "460300", month_spent=500.0, is_trading_day=True, policy=policy
            )
            self.assertEqual(line["today_amount"], 5.0, msg=f"raw={raw!r}")
            self.assertTrue(line["unlimited_monthly"], msg=f"raw={raw!r}")

    def test_non_trading_day_skips(self) -> None:
        for code in ("012773", "460300", "160119", "016452"):
            line = personal_dca_line(
                code, month_spent=0.0, is_trading_day=False, policy=load_policy()
            )
            self.assertEqual(line["today_amount"], 0.0)
            self.assertFalse(line["executable"])
            self.assertIn("非交易日", line["reason"])


class PersonalDcaMonitorTests(unittest.TestCase):
    """银行每天照扣，系统只给「维持 / 建议暂停 / 可考虑加码」的监控结论。"""

    def test_bond_sleeve_has_no_pe_gate(self) -> None:
        line = personal_dca_monitor("012773", policy=load_policy())
        self.assertEqual(line["verdict"], "maintain")
        self.assertIn("短债不看", line["verdict_reason"])

    def test_qdii_premium_pauses_nasdaq(self) -> None:
        line = personal_dca_monitor(
            "016452", percentile_10y=75.0, premium=0.0936, policy=load_policy()
        )
        self.assertEqual(line["verdict"], "pause")
        self.assertEqual(line["multiplier"], 0.0)
        self.assertIn("溢价", line["verdict_reason"])

    def test_qdii_premium_missing_fails_closed(self) -> None:
        line = personal_dca_monitor(
            "016452", percentile_10y=40.0, premium=None, policy=load_policy()
        )
        self.assertEqual(line["verdict"], "pause")
        self.assertIn("缺失", line["verdict_reason"])

    def test_ndx_uses_the_us_ladder_when_premium_is_cheap(self) -> None:
        line = personal_dca_monitor(
            "016452",
            percentile_10y=35.0,
            premium=0.0,
            drawdown_from_52w_high=0.2,
            policy=load_policy(),
        )
        self.assertEqual(line["verdict"], "boost")
        self.assertEqual(line["multiplier"], 3.0)

    def test_high_percentile_pauses_a_share_daily_dca(self) -> None:
        line = personal_dca_monitor(
            "460300",
            percentile_10y=92.0,
            drawdown_from_52w_high=0.1,
            policy=load_policy(),
        )
        self.assertEqual(line["verdict"], "pause")
        self.assertIn("≥ 90%", line["verdict_reason"])

    def test_mid_band_keeps_daily_dca_without_boost(self) -> None:
        line = personal_dca_monitor(
            "160119",
            percentile_10y=85.0,
            drawdown_from_52w_high=0.1,
            policy=load_policy(),
        )
        self.assertEqual(line["verdict"], "wait")
        self.assertEqual(line["multiplier"], 0.5)


if __name__ == "__main__":
    unittest.main()
