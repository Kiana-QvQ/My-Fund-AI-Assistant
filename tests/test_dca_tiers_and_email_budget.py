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
    resolve_dca_line,
)
from policy_rules import load_policy  # noqa: E402


class DcaBudgetTests(unittest.TestCase):
    def test_sleeve_helper_uses_weight(self) -> None:
        policy = load_policy()
        monthly, weekly = dca_amounts(1.0, policy=policy, weight=0.27)
        self.assertEqual(monthly, 81.0)
        self.assertEqual(weekly, 20.25)

    def test_paused_equity_flows_to_short_bond(self) -> None:
        policy = load_policy()
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
    def test_nasdaq_trading_day_schedule_stops_at_monthly_amount(self) -> None:
        policy = load_policy()
        line = personal_dca_line(
            "016452",
            month_spent=205.0,
            is_trading_day=True,
            policy=policy,
        )
        self.assertEqual(line["today_amount"], 10.0)
        self.assertEqual(line["month_remaining"], 10.0)
        self.assertTrue(line["executable"])

        stopped = personal_dca_line(
            "016452",
            month_spent=215.0,
            is_trading_day=True,
            policy=policy,
        )
        self.assertEqual(stopped["today_amount"], 0.0)
        self.assertIn("终止金额", stopped["reason"])

    def test_nasdaq_schedule_skips_non_trading_day(self) -> None:
        line = personal_dca_line(
            "016452",
            month_spent=0.0,
            is_trading_day=False,
            policy=load_policy(),
        )
        self.assertEqual(line["today_amount"], 0.0)
        self.assertIn("非交易日", line["reason"])


if __name__ == "__main__":
    unittest.main()
