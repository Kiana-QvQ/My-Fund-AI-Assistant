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


if __name__ == "__main__":
    unittest.main()
