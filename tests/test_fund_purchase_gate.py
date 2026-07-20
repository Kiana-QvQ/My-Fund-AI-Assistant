"""Tests for OTC purchase status gates on DCA/build plans."""

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

from fund_purchase_gate import purchase_gate  # noqa: E402
from investment_plan import (  # noqa: E402
    allocate_dca_plan,
    apply_dca_purchase_gates,
    resolve_dca_line,
)
from policy_rules import load_policy  # noqa: E402
import send_trade_alert_email as email_mod  # noqa: E402


class PurchaseGateTests(unittest.TestCase):
    def test_suspend_blocks(self) -> None:
        ok, reason = purchase_gate(
            100.0,
            purchase_status="暂停申购",
            daily_limit=1000.0,
            minimum_purchase=10.0,
        )
        self.assertFalse(ok)
        self.assertIn("暂停申购", reason)

    def test_daily_limit_blocks(self) -> None:
        ok, reason = purchase_gate(
            50.0,
            purchase_status="限大额",
            daily_limit=10.0,
            minimum_purchase=10.0,
        )
        self.assertFalse(ok)
        self.assertIn("日限额", reason)

    def test_open_ok(self) -> None:
        ok, _ = purchase_gate(
            28.0,
            purchase_status="开放申购",
            daily_limit=1e12,
            minimum_purchase=10.0,
        )
        self.assertTrue(ok)


class DcaPurchaseGateIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()

    def test_spx_suspend_blocks_even_if_premium_ok(self) -> None:
        snap = {
            "as_of": "2026-07-18",
            "funds": {
                "050025": {
                    "purchase_status": "暂停申购",
                    "daily_limit": 100.0,
                    "minimum_purchase": 10.0,
                },
                "012773": {
                    "purchase_status": "限大额",
                    "daily_limit": 1e9,
                    "minimum_purchase": 10.0,
                },
            },
            "indexes": {
                "沪深300": {
                    "pe_percentile": 74.0,
                    "drawdown_from_52w_high": 0.1,
                    "verified": True,
                    "tradeable": True,
                },
                "中证500": {
                    "pe_percentile": 82.0,
                    "drawdown_from_52w_high": 0.1,
                    "verified": True,
                    "tradeable": True,
                },
                "标普500": {
                    "pe_percentile": 50.0,
                    "drawdown_from_52w_high": 0.1,
                    "qdii_premium": 0.0,
                    "verified": True,
                    "tradeable": True,
                },
                "纳斯达克100": {
                    "pe_percentile": None,
                    "reference_only": True,
                    "verified": False,
                    "tradeable": False,
                },
            },
        }
        lines = email_mod.collect_dca(snap, self.policy, today=date(2026, 7, 18))
        spx = next(ln for ln in lines if ln["name"] == "标普500")
        self.assertTrue(spx["paused"])
        self.assertEqual(float(spx["weekly"]), 0.0)
        short = next(ln for ln in lines if ln["name"] == "短债")
        self.assertGreater(float(short["weekly"]), 0.0)

    def test_tiny_equity_merges_into_short_bond(self) -> None:
        equity = [
            resolve_dca_line(
                "沪深300",
                80.0,
                drawdown_from_52w_high=0.05,
                policy=self.policy,
            ),
            resolve_dca_line(
                "中证500",
                80.0,
                drawdown_from_52w_high=0.05,
                policy=self.policy,
            ),
            resolve_dca_line(
                "标普500",
                93.0,
                drawdown_from_52w_high=0.05,
                premium=0.05,
                verified=True,
                tradeable=True,
                policy=self.policy,
            ),
            resolve_dca_line("纳斯达克100", 10.0, policy=self.policy),
        ]
        lines = allocate_dca_plan(
            equity,
            policy=self.policy,
            today=date(2026, 7, 18),
            month_spent=0.0,
        )
        snap = {
            "funds": {
                "460300": {
                    "purchase_status": "开放申购",
                    "daily_limit": 1e9,
                    "minimum_purchase": 10.0,
                },
                "160119": {
                    "purchase_status": "开放申购",
                    "daily_limit": 1e9,
                    "minimum_purchase": 10.0,
                },
                "012773": {
                    "purchase_status": "限大额",
                    "daily_limit": 1e9,
                    "minimum_purchase": 10.0,
                },
            }
        }
        out = apply_dca_purchase_gates(lines, snap, policy=self.policy)
        csi500 = next(ln for ln in out if ln["name"] == "中证500")
        self.assertEqual(float(csi500["weekly"]), 0.0)
        self.assertIn("并入短债", csi500["reason"])


class BuildPurchaseGateTests(unittest.TestCase):
    def test_build_blocks_on_suspend(self) -> None:
        snap = {
            "funds": {
                "050025": {
                    "purchase_status": "暂停申购",
                    "daily_limit": 100.0,
                    "minimum_purchase": 10.0,
                }
            },
            "indexes": {
                "标普500": {
                    "pe_percentile": 50.0,
                    "pe_percentile_1y": 40.0,
                    "drawdown_from_52w_high": 0.06,
                    "qdii_premium": 0.0,
                    "verified": True,
                    "tradeable": True,
                }
            },
        }
        with unittest.mock.patch.object(email_mod, "holdings_cost", return_value={}), unittest.mock.patch.object(
            email_mod, "building_principal", return_value=10000.0
        ):
            lines = email_mod.collect_build(snap, load_policy())
        spx = next(ln for ln in lines if ln["name"] == "标普500")
        self.assertFalse(spx["active"])
        self.assertIn("暂停申购", spx["reason"])
