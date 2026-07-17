"""Hardening tests: contracts, e2e signal parity, holiday merge, calendar retry."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import a_share_pe  # noqa: E402
import record_holding as ledger  # noqa: E402
import send_trade_alert_email as email_mod  # noqa: E402
import trading_calendar as calendar  # noqa: E402
import update_portfolio_readme as readme_mod  # noqa: E402
import us_pe  # noqa: E402
from policy_rules import load_policy  # noqa: E402


SAMPLE_INDEXES = {
    "沪深300": {
        "pe_ttm": 13.0,
        "pe_percentile": 55.0,
        "pe_percentile_1y": 50.0,
        "verified": True,
        "tradeable": True,
        "date": "2026-07-16",
    },
    "中证500": {
        "pe_ttm": 20.0,
        "pe_percentile": 55.0,
        "pe_percentile_1y": 50.0,
        "verified": True,
        "tradeable": True,
        "date": "2026-07-16",
    },
    "标普500": {
        "pe_ttm": 20.0,
        "pe_percentile": 40.0,
        "pe_percentile_1y": 25.0,
        "qdii_premium": 0.03,
        "qdii_premium_pct": 3.0,
        "verified": True,
        "tradeable": True,
        "date": "2026-07-16",
    },
    "纳斯达克100": {
        "pe_ttm": 32.5,
        "pe_percentile": None,
        "pe_percentile_1y": None,
        "verified": False,
        "tradeable": False,
        "reference_only": True,
        "date": "2026-07-17",
    },
}


class ContractTests(unittest.TestCase):
    def test_multpl_current_html_contract(self) -> None:
        html = """
        <div id="current"><b>Current S&amp;P 500 PE Ratio:</b> 32.41</div>
        <table></table>
        <div id="timestamp">4:00 PM EDT, Thu Jul 16</div>
        """
        parsed = us_pe.parse_multpl_current_html(html, today=us_pe._today())
        us_pe.assert_multpl_current_contract(parsed)
        self.assertEqual(parsed["pe_ttm"], 32.41)

    def test_multpl_monthly_too_few_points_fails(self) -> None:
        html = "<table><tr><td>Jul 1, 2026</td><td>30.0</td></tr></table>"
        with self.assertRaises(RuntimeError):
            us_pe.parse_multpl_monthly_html(html)

    def test_stockanalysis_qqq_contract(self) -> None:
        html = (
            "<html><body>QQQ ETF"
            "<table><tr><td>PE Ratio</td><td>32.54</td></tr></table>"
            "</body></html>"
        )
        pe = us_pe.parse_stockanalysis_qqq_pe(html)
        self.assertEqual(pe, 32.54)

    def test_stockanalysis_missing_field_fails(self) -> None:
        with self.assertRaises(RuntimeError):
            us_pe.parse_stockanalysis_qqq_pe("<html>QQQ without pe</html>")

    def test_akshare_history_contract(self) -> None:
        frame = pd.DataFrame({"日期": ["2026-01-01"], "滚动市盈率": [12.0]})
        a_share_pe.assert_akshare_pe_history_contract(frame, "沪深300")
        with self.assertRaises(RuntimeError):
            a_share_pe.assert_akshare_pe_history_contract(
                pd.DataFrame({"date": ["2026-01-01"]}), "沪深300"
            )


class HolidayMergeAndReadmeE2ETests(unittest.TestCase):
    def test_us_only_merge_preserves_qdii_and_meta(self) -> None:
        market = {
            "as_of": "2026-07-17",
            "indexes": {
                "标普500": {
                    "pe_ttm": 30.0,
                    "qdii_premium": 0.012,
                    "qdii_premium_pct": 1.2,
                    "qdii_etf": "513500",
                }
            },
        }
        us = {
            "indexes": {
                "标普500": {
                    "pe_ttm": 32.41,
                    "pe_percentile": 93.33,
                    "verified": True,
                    "tradeable": True,
                },
                "纳斯达克100": {
                    "pe_ttm": 32.54,
                    "pe_percentile": None,
                    "verified": False,
                    "tradeable": False,
                },
            },
            "us_decision_blocked": False,
            "nasdaq_buy_blocked": True,
            "alerts": [],
        }
        merged = us_pe.merge_us_into_market_snapshot(market, us)
        spx = merged["indexes"]["标普500"]
        self.assertEqual(spx["pe_ttm"], 32.41)
        self.assertEqual(spx["qdii_premium_pct"], 1.2)
        self.assertIn("纳斯达克100", merged["indexes"])
        self.assertFalse(merged["us_meta"]["us_decision_blocked"])
        self.assertTrue(merged["us_meta"]["nasdaq_buy_blocked"])

    def test_readme_render_from_fixture_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            holdings = {
                "building_principal": 10000.0,
                "initial_build_percent": 20.0,
                "holdings": [
                    {
                        "fund_code": "012773",
                        "name": "短债",
                        "cost_basis": 2000.0,
                        "target_percent": 51.0,
                        "asset_class": "短债基金",
                    }
                ],
            }
            snapshot = {"as_of": "2026-07-17", "indexes": SAMPLE_INDEXES}
            (root / "config").mkdir()
            (root / "data").mkdir()
            holdings_path = root / "config" / "portfolio_holdings.json"
            snap_path = root / "data" / "market_snapshot.json"
            status_path = root / "data" / "portfolio_status.json"
            readme_path = root / "README.md"
            holdings_path.write_text(
                json.dumps(holdings, ensure_ascii=False), encoding="utf-8"
            )
            snap_path.write_text(
                json.dumps(snapshot, ensure_ascii=False), encoding="utf-8"
            )
            readme_path.write_text(
                "前置\n\n<!-- PORTFOLIO_STATUS_START -->\nold\n"
                "<!-- PORTFOLIO_STATUS_END -->\n\n后置\n",
                encoding="utf-8",
            )
            old = {
                "HOLDINGS_PATH": readme_mod.HOLDINGS_PATH,
                "SNAPSHOT_PATH": readme_mod.SNAPSHOT_PATH,
                "STATUS_PATH": readme_mod.STATUS_PATH,
                "README_PATH": readme_mod.README_PATH,
            }
            try:
                readme_mod.HOLDINGS_PATH = holdings_path
                readme_mod.SNAPSHOT_PATH = snap_path
                readme_mod.STATUS_PATH = status_path
                readme_mod.README_PATH = readme_path
                readme_mod.main()
                text = readme_path.read_text(encoding="utf-8")
                self.assertIn("PORTFOLIO_STATUS_START", text)
                self.assertIn("溢价过高", text)
                self.assertIn("无统计分位", text)
                status = json.loads(status_path.read_text(encoding="utf-8"))
                self.assertIn("overall_decision", status)
            finally:
                for key, value in old.items():
                    setattr(readme_mod, key, value)

    def test_email_and_readme_share_same_premium_signal(self) -> None:
        policy = load_policy()
        snapshot = {"as_of": "2026-07-17", "indexes": SAMPLE_INDEXES, "us_meta": {}}
        with patch.object(email_mod, "holdings_cost", return_value={}), patch.object(
            email_mod, "building_principal", return_value=10000.0
        ):
            email_data = email_mod.collect_signals(snapshot, 300.0, policy)
        tone, notes = readme_mod.summarize_equity(
            SAMPLE_INDEXES, holdings_cost={}, principal=10000.0
        )
        self.assertIn("溢价", tone)
        self.assertTrue(any("溢价过高" in row for row in email_data["rows"]))
        self.assertTrue(any("溢价" in n for n in notes))
        self.assertFalse(email_data["has_buy"])


class LedgerIdempotencyAndSetAuditTests(unittest.TestCase):
    def test_duplicate_buy_rejected_then_force(self) -> None:
        doc = {"holdings": [], "transactions": []}
        ledger.apply_buy(
            doc, "460300", 270.0, "定投", transaction_id="tx-buy-1"
        )
        with self.assertRaises(SystemExit):
            ledger.apply_buy(
                doc, "460300", 270.0, "定投", transaction_id="tx-buy-1"
            )
        self.assertEqual(doc["holdings"][0]["cost_basis"], 270.0)
        self.assertEqual(len(doc["transactions"]), 1)
        ledger.apply_buy(
            doc,
            "460300",
            270.0,
            "定投",
            transaction_id="tx-buy-1",
            force_duplicate=True,
        )
        self.assertEqual(doc["holdings"][0]["cost_basis"], 540.0)
        self.assertEqual(len(doc["transactions"]), 2)

    def test_set_records_before_after(self) -> None:
        doc = {
            "holdings": [
                {
                    "fund_code": "460300",
                    "name": "x",
                    "shares": 10.0,
                    "cost_basis": 100.0,
                    "target_percent": 27.0,
                    "asset_class": "A股宽基",
                }
            ],
            "transactions": [],
        }
        ledger.apply_set(doc, "460300", 250.0, 20.0, "对账修正")
        tx = doc["transactions"][-1]
        self.assertEqual(tx["before_cost"], 100.0)
        self.assertEqual(tx["after_cost"], 250.0)
        self.assertEqual(tx["before_shares"], 10.0)
        self.assertEqual(tx["after_shares"], 20.0)
        self.assertEqual(tx["cost_delta"], 150.0)
        self.assertEqual(tx["reason"], "对账修正")
        self.assertIn("transaction_id", tx)


class TradingCalendarRetryTests(unittest.TestCase):
    def test_resolve_trading_day_retries_then_ok(self) -> None:
        calls = {"n": 0}

        def flaky(_day=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("network")
            return True

        with patch.object(calendar, "is_a_share_trading_day", side_effect=flaky), patch(
            "time.sleep"
        ):
            self.assertTrue(calendar.resolve_trading_day("2026-07-17", retries=3))
        self.assertEqual(calls["n"], 3)

    def test_resolve_trading_day_fails_after_retries(self) -> None:
        with patch.object(
            calendar,
            "is_a_share_trading_day",
            side_effect=RuntimeError("down"),
        ), patch("time.sleep"):
            with self.assertRaises(RuntimeError):
                calendar.resolve_trading_day("2026-07-17", retries=2)


class WorkflowConcurrencyTests(unittest.TestCase):
    def test_workflow_has_concurrency_and_checks(self) -> None:
        text = (ROOT / ".github" / "workflows" / "portfolio-update.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("concurrency:", text)
        self.assertIn("group: portfolio-dashboard", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("merge_us_into_market_snapshot", text)


if __name__ == "__main__":
    unittest.main()
