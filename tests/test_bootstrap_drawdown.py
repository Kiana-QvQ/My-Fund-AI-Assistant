"""Tests for relaxed 10y DCA tiers and 1y build 100/50/25%."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from index_drawdown import attach_drawdowns, compute_drawdown_from_closes  # noqa: E402
from policy_rules import load_policy, resolve_action, try_bootstrap_action  # noqa: E402


class DrawdownMathTests(unittest.TestCase):
    def test_compute_drawdown_from_closes(self) -> None:
        closes = [80.0] * 100 + [100.0] + [90.0] * 50 + [85.0]
        metrics = compute_drawdown_from_closes(closes)
        self.assertAlmostEqual(metrics["drawdown_from_52w_high"], 0.15, places=4)


class DcaAndBuildTierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()

    def test_scheme_c_10y_tiers(self) -> None:
        cases = [
            (39.0, "triple"),
            (45.0, "double"),
            (55.0, "sesqui"),
            (65.0, "buy"),
            (75.0, "light"),
            (85.0, "half"),
            (90.0, "overvalued_watch"),
        ]
        for pct, expected in cases:
            action, _ = resolve_action(
                "沪深300",
                pct,
                percentile_1y=95.0,
                drawdown_from_52w_high=0.0,
                policy=self.policy,
                held_cost=0.0,
                target_amount=2700.0,
            )
            self.assertEqual(action, expected, f"pct={pct}")

    def test_current_levels_get_half_dca(self) -> None:
        for name, pct10, pct1y, dd, target in (
            ("沪深300", 80.06, 72.43, 0.0714, 2700.0),
            ("中证500", 85.78, 85.6, 0.1191, 1100.0),
        ):
            action, reason = resolve_action(
                name,
                pct10,
                percentile_1y=pct1y,
                drawdown_from_52w_high=dd,
                policy=self.policy,
                held_cost=0.0,
                target_amount=target,
            )
            self.assertEqual(action, "half", name)
            self.assertIn("50%", reason)

    def test_1y_build_100_tier(self) -> None:
        action, reason = resolve_action(
            "沪深300",
            82.0,  # would be half on 10y
            percentile_1y=45.0,
            drawdown_from_52w_high=0.06,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        # 1y 100% > 10y 50% → upgrade to buy
        self.assertEqual(action, "buy")
        self.assertIn("1年建仓", reason)

    def test_1y_build_25_when_10y_stopped(self) -> None:
        action, reason = resolve_action(
            "沪深300",
            92.0,
            percentile_1y=80.0,
            drawdown_from_52w_high=0.05,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertEqual(action, "bootstrap")
        self.assertIn("25%", reason)

    def test_drawdown_blocks_1y_upgrade_keeps_half(self) -> None:
        action, reason = resolve_action(
            "沪深300",
            75.0,
            percentile_1y=45.0,
            drawdown_from_52w_high=0.02,  # too shallow for 100%/50% tiers
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertEqual(action, "light")
        self.assertIn("70%", reason)

    def test_main_10y_buy_unchanged_band(self) -> None:
        action, _ = resolve_action(
            "沪深300",
            65.0,
            percentile_1y=90.0,
            drawdown_from_52w_high=0.0,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertEqual(action, "buy")

    def test_us_half_extends_to_90(self) -> None:
        action, reason = resolve_action(
            "标普500",
            85.0,
            percentile_1y=90.0,
            drawdown_from_52w_high=0.0,
            policy=self.policy,
            verified=True,
            tradeable=True,
            held_cost=0.0,
            target_amount=800.0,
        )
        self.assertEqual(action, "half")
        self.assertIn("50%", reason)

    def test_us_light_tier(self) -> None:
        action, reason = resolve_action(
            "标普500",
            75.0,
            percentile_1y=90.0,
            drawdown_from_52w_high=0.0,
            policy=self.policy,
            verified=True,
            tradeable=True,
            held_cost=0.0,
            target_amount=800.0,
        )
        self.assertEqual(action, "light")
        self.assertIn("70%", reason)

    def test_missing_drawdown_fail_closed_on_build(self) -> None:
        result = try_bootstrap_action(
            "沪深300",
            percentile=50.0,
            percentile_1y=40.0,
            drawdown_from_52w_high=None,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertIsNotNone(result)
        assert result is not None
        action, reason, frac = result
        self.assertEqual(action, "wait")
        self.assertEqual(frac, 0.0)
        self.assertIn("失败关闭", reason)

    def test_ndx_never_builds(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
