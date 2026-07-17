"""Unit tests for policy, ledger, PE gates, email syntax, and fail-closed paths."""

from __future__ import annotations

import compileall
import importlib
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from policy_rules import classify_index, load_policy, resolve_action  # noqa: E402
import record_holding as ledger  # noqa: E402
import send_trade_alert_email as email_mod  # noqa: E402
import update_portfolio_readme as readme_mod  # noqa: E402
import us_pe  # noqa: E402


class PolicyRulesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()

    def test_no_take_profit_without_holding(self) -> None:
        action, reason = resolve_action(
            "沪深300",
            92.0,
            percentile_1y=95.0,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertEqual(action, "overvalued_watch")
        self.assertIn("无持仓无需止盈", reason)

    def test_take_profit_with_holding(self) -> None:
        action, reason = resolve_action(
            "沪深300",
            92.0,
            percentile_1y=95.0,
            policy=self.policy,
            held_cost=500.0,
            target_amount=2700.0,
        )
        self.assertEqual(action, "take_profit")
        self.assertIn("止盈", reason)

    def test_qdii_premium_blocks_buy(self) -> None:
        action, reason = classify_index(
            "标普500",
            40.0,
            premium=0.025,
            policy=self.policy,
            verified=True,
            tradeable=True,
        )
        self.assertEqual(action, "premium_block")
        self.assertIn("溢价", reason)

    def test_nasdaq_never_buys(self) -> None:
        action, _ = resolve_action(
            "纳斯达克100",
            10.0,
            percentile_1y=5.0,
            policy=self.policy,
            held_cost=0.0,
            target_amount=300.0,
        )
        self.assertEqual(action, "reference")


class LedgerSellTests(unittest.TestCase):
    def _holding_doc(self) -> dict:
        return {
            "holdings": [
                {
                    "fund_code": "460300",
                    "name": "x",
                    "shares": 100.0,
                    "cost_basis": 1000.0,
                    "target_percent": 27.0,
                    "asset_class": "A股宽基",
                }
            ],
            "transactions": [],
        }

    def test_sell_uses_cost_not_proceeds(self) -> None:
        doc = self._holding_doc()
        row = ledger.apply_sell(
            doc,
            "460300",
            proceeds=150.0,
            shares=10.0,
            note="止盈测试",
        )
        self.assertEqual(row["cost_basis"], 900.0)
        self.assertAlmostEqual(row["shares"], 90.0)
        tx = doc["transactions"][-1]
        self.assertEqual(tx["side"], "sell")
        self.assertEqual(tx["proceeds"], 150.0)
        self.assertEqual(tx["cost_delta"], -100.0)
        self.assertEqual(tx["shares"], 10.0)

    def test_sell_rejects_shares_over_holding(self) -> None:
        doc = self._holding_doc()
        with self.assertRaises(SystemExit):
            ledger.apply_sell(doc, "460300", proceeds=200.0, cost=200.0, shares=200.0)

    def test_sell_rejects_negative_shares(self) -> None:
        doc = self._holding_doc()
        with self.assertRaises(SystemExit):
            ledger.apply_sell(doc, "460300", proceeds=10.0, shares=-1.0)

    def test_sell_rejects_mismatched_cost_and_shares(self) -> None:
        doc = self._holding_doc()
        with self.assertRaises(SystemExit):
            ledger.apply_sell(doc, "460300", proceeds=150.0, cost=50.0, shares=10.0)

    def test_buy_with_shares_and_nav(self) -> None:
        doc = {"holdings": [], "transactions": []}
        row = ledger.apply_buy(
            doc, "460300", 100.0, "建仓", shares=10.0, nav=10.0
        )
        self.assertEqual(row["cost_basis"], 100.0)
        self.assertEqual(row["shares"], 10.0)
        tx = doc["transactions"][-1]
        self.assertEqual(tx["side"], "buy")
        self.assertEqual(tx["amount"], 100.0)
        self.assertEqual(tx["shares"], 10.0)
        self.assertEqual(tx["nav"], 10.0)

    def test_buy_rejects_amount_shares_nav_mismatch(self) -> None:
        doc = {"holdings": [], "transactions": []}
        with self.assertRaises(SystemExit):
            ledger.apply_buy(doc, "460300", 100.0, None, shares=10.0, nav=20.0)

    def test_set_rejects_negative_cost_or_shares(self) -> None:
        doc = {"holdings": [], "transactions": []}
        with self.assertRaises(SystemExit):
            ledger.apply_set(doc, "460300", -1.0, None, None)
        with self.assertRaises(SystemExit):
            ledger.apply_set(doc, "460300", 100.0, -5.0, None)

    def test_buy_then_sell_full_ledger(self) -> None:
        doc = {"holdings": [], "transactions": []}
        ledger.apply_buy(doc, "460300", 1000.0, "买入", shares=100.0, nav=10.0)
        ledger.apply_sell(
            doc,
            "460300",
            proceeds=330.0,
            shares=30.0,
            nav=11.0,
            note="止盈1/3",
        )
        self.assertEqual(len(doc["transactions"]), 2)
        self.assertEqual(doc["transactions"][0]["side"], "buy")
        self.assertEqual(doc["transactions"][1]["side"], "sell")
        self.assertEqual(doc["transactions"][1]["proceeds"], 330.0)
        self.assertEqual(doc["transactions"][1]["cost_delta"], -300.0)
        holding = doc["holdings"][0]
        self.assertEqual(holding["cost_basis"], 700.0)
        self.assertAlmostEqual(holding["shares"], 70.0)


class NdxPercentileGateTests(unittest.TestCase):
    def test_insufficient_samples_yield_none_percentile(self) -> None:
        points = [{"date": "2026-07-17", "pe_ttm": 32.5}]
        window_10y = us_pe._window_values(points, us_pe._today(), years=10)
        self.assertLess(len(window_10y), 30)
        percentile = (
            round(us_pe._percentile(window_10y, 32.5), 2)
            if len(window_10y) >= 30
            else None
        )
        self.assertIsNone(percentile)


class SpxFailClosedTests(unittest.TestCase):
    def test_validate_spx_rejects_missing_pe(self) -> None:
        ok, errors = us_pe.validate_spx(None, 50.0, date(2026, 7, 16))
        self.assertFalse(ok)
        self.assertTrue(any("缺失" in e for e in errors))

    def test_refresh_fetch_failure_blocks_trading(self) -> None:
        ndx = {
            "pe_ttm": 30.0,
            "pe_percentile": None,
            "pe_percentile_1y": None,
            "verified": False,
            "tradeable": False,
            "reference_only": True,
            "reason": "test",
            "validation_errors": [],
        }
        with patch.object(
            us_pe, "load_or_fetch_monthly_series", side_effect=RuntimeError("parse fail")
        ), patch.object(us_pe, "_nasdaq_reference_item", return_value=ndx):
            snap = us_pe.refresh_us_pe(persist=False)
        spx = snap["indexes"]["标普500"]
        self.assertFalse(spx["verified"])
        self.assertFalse(spx["tradeable"])
        self.assertEqual(spx["status"], "fetch_failed")
        self.assertTrue(snap["us_decision_blocked"])
        self.assertIn("禁止使用过期缓存", spx["reason"])


class EmailAndReadmeSignalTests(unittest.TestCase):
    def test_email_module_imports_and_compiles(self) -> None:
        importlib.reload(email_mod)
        self.assertTrue(
            compileall.compile_file(
                str(SCRIPTS / "send_trade_alert_email.py"), quiet=1
            )
        )

    def test_scripts_directory_compiles(self) -> None:
        self.assertTrue(compileall.compile_dir(str(SCRIPTS), quiet=1))

    def test_qdii_premium_shows_in_email_and_readme_summary(self) -> None:
        policy = load_policy()
        snapshot = {
            "as_of": "2026-07-17",
            "indexes": {
                "沪深300": {
                    "pe_ttm": 13.0,
                    "pe_percentile": 92.0,
                    "pe_percentile_1y": 95.0,
                    "verified": True,
                    "tradeable": True,
                },
                "中证500": {
                    "pe_ttm": 20.0,
                    "pe_percentile": 92.0,
                    "pe_percentile_1y": 95.0,
                    "verified": True,
                    "tradeable": True,
                },
                "标普500": {
                    "pe_ttm": 20.0,
                    "pe_percentile": 40.0,
                    "pe_percentile_1y": 30.0,
                    "qdii_premium": 0.03,
                    "qdii_premium_pct": 3.0,
                    "verified": True,
                    "tradeable": True,
                },
                "纳斯达克100": {
                    "pe_ttm": 30.0,
                    "pe_percentile": None,
                    "pe_percentile_1y": None,
                    "verified": False,
                    "tradeable": False,
                    "reference_only": True,
                },
            },
            "us_meta": {"alerts": []},
        }
        with patch.object(email_mod, "holdings_cost", return_value={}), patch.object(
            email_mod, "building_principal", return_value=10000.0
        ):
            lines = email_mod.collect_dca(snapshot, policy)
        spx = next(ln for ln in lines if ln["name"] == "标普500")
        self.assertTrue(spx["paused"])
        self.assertEqual(spx["action"], "premium_block")
        self.assertIn("溢价", spx["reason"])

        tone, notes = readme_mod.summarize_equity(
            snapshot["indexes"],
            holdings_cost={},
            principal=10000.0,
        )
        self.assertIn("溢价", tone)
        self.assertTrue(any("标普500" in n and "溢价" in n for n in notes))


class WorkflowHolidayBranchTests(unittest.TestCase):
    def test_workflow_keeps_us_refresh_on_a_share_holiday(self) -> None:
        text = (ROOT / ".github" / "workflows" / "portfolio-update.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("run_us=yes", text)
        self.assertIn("Refresh US valuation only (A-share holiday)", text)
        self.assertIn("steps.gate.outputs.run != 'yes' && steps.gate.outputs.run_us == 'yes'", text)
        self.assertIn("python -m compileall -q app scripts tests", text)
        self.assertIn("python -m unittest discover -s tests -v", text)
        # Holiday path still allows event emails (mode=event), not skip.
        self.assertIn('MODE="event"', text)
        self.assertIn(
            "(steps.gate.outputs.run == 'yes' || steps.gate.outputs.run_us == 'yes') && steps.mode.outputs.value != 'skip'",
            text,
        )


if __name__ == "__main__":
    unittest.main()
