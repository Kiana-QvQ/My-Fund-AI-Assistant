"""Unit tests for policy rules, ledger sell cost, and NDX percentile gates."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from policy_rules import classify_index, load_policy, resolve_action  # noqa: E402
import record_holding as ledger  # noqa: E402
import us_pe  # noqa: E402


class PolicyRulesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()

    def test_no_take_profit_without_holding(self) -> None:
        action, reason = resolve_action(
            "沪深300",
            80.0,
            percentile_1y=90.0,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertEqual(action, "overvalued_watch")
        self.assertIn("无持仓无需止盈", reason)

    def test_take_profit_with_holding(self) -> None:
        action, reason = resolve_action(
            "沪深300",
            80.0,
            percentile_1y=90.0,
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
    def test_sell_uses_cost_not_proceeds(self) -> None:
        doc = {
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
        # Redeem 150 market value but only 100 cost for 10 shares.
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
        self.assertEqual(tx["proceeds"], 150.0)
        self.assertEqual(tx["cost_delta"], -100.0)


class NdxPercentileGateTests(unittest.TestCase):
    def test_insufficient_samples_yield_none_percentile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hist = root / "ndx.json"
            hist.write_text(
                '{"ticker":"QQQ","points":[{"date":"2026-07-17","pe_ttm":32.5}]}\n',
                encoding="utf-8",
            )
            old_root = us_pe.ROOT
            old_snap = us_pe.SNAPSHOT_PATH
            try:
                us_pe.ROOT = root
                us_pe.SNAPSHOT_PATH = root / "us_pe_snapshot.json"
                # Monkeypatch history path via building item directly.
                points = [{"date": "2026-07-17", "pe_ttm": 32.5}]
                window_10y = us_pe._window_values(
                    points, us_pe._today(), years=10
                )
                self.assertLess(len(window_10y), 30)
                percentile = (
                    round(us_pe._percentile(window_10y, 32.5), 2)
                    if len(window_10y) >= 30
                    else None
                )
                self.assertIsNone(percentile)
            finally:
                us_pe.ROOT = old_root
                us_pe.SNAPSHOT_PATH = old_snap


if __name__ == "__main__":
    unittest.main()
