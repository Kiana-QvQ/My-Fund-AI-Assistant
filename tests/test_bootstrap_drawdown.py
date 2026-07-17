"""Tests for per-index micro bootstrap (1y PE + 52w drawdown + optional 10y cap)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from index_drawdown import attach_drawdowns, compute_drawdown_from_closes  # noqa: E402
from policy_rules import (  # noqa: E402
    bootstrap_planned_amount,
    load_policy,
    resolve_action,
    try_bootstrap_action,
)


class DrawdownMathTests(unittest.TestCase):
    def test_compute_drawdown_from_closes(self) -> None:
        closes = [80.0] * 100 + [100.0] + [90.0] * 50 + [85.0]
        metrics = compute_drawdown_from_closes(closes)
        self.assertAlmostEqual(metrics["drawdown_from_52w_high"], 0.15, places=4)
        self.assertEqual(metrics["close"], 85.0)
        self.assertEqual(metrics["high_52w"], 100.0)


class BootstrapDrawdownGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()

    def test_hs300_micro_in_high_10y_zone(self) -> None:
        # 10y 80% would be overvalued for main DCA, but micro may still open.
        action, reason = resolve_action(
            "沪深300",
            80.0,
            percentile_1y=52.0,
            drawdown_from_52w_high=0.09,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertEqual(action, "bootstrap")
        self.assertIn("微建仓", reason)
        self.assertAlmostEqual(
            bootstrap_planned_amount(0.0, 2700.0, self.policy), 135.0, places=2
        )

    def test_csi500_blocked_by_10y_safety_cap(self) -> None:
        action, reason = resolve_action(
            "中证500",
            85.78,
            percentile_1y=66.0,
            drawdown_from_52w_high=0.11,
            policy=self.policy,
            held_cost=0.0,
            target_amount=1100.0,
        )
        self.assertNotEqual(action, "bootstrap")
        self.assertIn("85", reason)

    def test_low_1y_pe_without_enough_drawdown_keeps_half(self) -> None:
        action, reason = resolve_action(
            "沪深300",
            55.0,
            percentile_1y=25.0,
            drawdown_from_52w_high=0.05,  # < 6%
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertEqual(action, "half")
        self.assertIn("基础定投", reason)

    def test_drawdown_alone_never_upgrades_to_bootstrap(self) -> None:
        action, reason = resolve_action(
            "沪深300",
            55.0,
            percentile_1y=65.0,  # > 60% HS300 micro gate
            drawdown_from_52w_high=0.25,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertEqual(action, "half")
        self.assertNotIn("微建仓", reason)

    def test_10y_at_or_above_85_blocks_hs300(self) -> None:
        action, _ = resolve_action(
            "沪深300",
            85.0,
            percentile_1y=40.0,
            drawdown_from_52w_high=0.10,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertNotEqual(action, "bootstrap")

    def test_missing_drawdown_fail_closed_on_starter(self) -> None:
        result = try_bootstrap_action(
            "沪深300",
            percentile=50.0,
            percentile_1y=20.0,
            drawdown_from_52w_high=None,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertIsNotNone(result)
        assert result is not None
        action, reason = result
        self.assertEqual(action, "wait")
        self.assertIn("失败关闭", reason)

    def test_main_10y_buy_unchanged(self) -> None:
        action, _ = resolve_action(
            "沪深300",
            35.0,
            percentile_1y=80.0,
            drawdown_from_52w_high=0.0,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertEqual(action, "buy")

    def test_scheme_b_tiers(self) -> None:
        cases = [
            (25.0, "double"),
            (30.0, "buy"),
            (39.9, "buy"),
            (40.0, "half"),
            (59.9, "half"),
            (60.0, "overvalued_watch"),
        ]
        for pct, expected in cases:
            action, _ = resolve_action(
                "沪深300",
                pct,
                percentile_1y=90.0,
                drawdown_from_52w_high=0.0,
                policy=self.policy,
                held_cost=0.0,
                target_amount=2700.0,
            )
            self.assertEqual(action, expected, f"pct={pct}")

    def test_us_half_tier(self) -> None:
        action, reason = resolve_action(
            "标普500",
            55.0,
            percentile_1y=80.0,
            drawdown_from_52w_high=0.0,
            policy=self.policy,
            verified=True,
            tradeable=True,
            held_cost=0.0,
            target_amount=800.0,
        )
        self.assertEqual(action, "half")
        self.assertIn("50%", reason)

    def test_spx_micro_requires_verified_and_drawdown(self) -> None:
        action, reason = resolve_action(
            "标普500",
            75.0,
            percentile_1y=55.0,
            drawdown_from_52w_high=0.09,
            premium=0.01,
            policy=self.policy,
            verified=True,
            tradeable=True,
            held_cost=0.0,
            target_amount=800.0,
        )
        self.assertEqual(action, "bootstrap")
        self.assertIn("微建仓", reason)

    def test_ndx_never_micro(self) -> None:
        action, _ = resolve_action(
            "纳斯达克100",
            50.0,
            percentile_1y=10.0,
            drawdown_from_52w_high=0.20,
            policy=self.policy,
            verified=False,
            tradeable=False,
            held_cost=0.0,
            target_amount=300.0,
        )
        self.assertEqual(action, "reference")

    def test_attach_drawdowns_merges_fields(self) -> None:
        indexes = {"沪深300": {"pe_percentile": 50.0}}

        def fake_fetch(name: str) -> dict:
            return {
                "close": 4500.0,
                "high_52w": 5000.0,
                "drawdown_from_52w_high": 0.1,
                "drawdown_from_52w_high_pct": 10.0,
                "source": "test",
                "status": "ok",
            }

        attach_drawdowns(indexes, names=("沪深300",), fetcher=fake_fetch)
        self.assertEqual(indexes["沪深300"]["drawdown_from_52w_high"], 0.1)
        self.assertEqual(indexes["沪深300"]["drawdown_from_52w_high_pct"], 10.0)


if __name__ == "__main__":
    unittest.main()
