"""Tests for independent DCA multipliers and build tiers."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from investment_plan import (  # noqa: E402
    allocate_dca_plan,
    portfolio_monthly_base,
    portfolio_monthly_cap,
    remaining_thursdays,
    resolve_build_line,
    resolve_dca_line,
    thursdays_in_month,
)
from policy_rules import load_policy, resolve_action  # noqa: E402


class DcaMultiplierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()

    def test_dca_tiers_a_and_us_ladder(self) -> None:
        cases = [
            (39.9, 3.0),
            (40.0, 2.0),
            (49.9, 2.0),
            (50.0, 1.5),
            (59.9, 1.5),
            (60.0, 1.0),
            (69.9, 1.0),
            (70.0, 0.7),
            (79.9, 0.7),
            (80.0, 0.5),
            (89.9, 0.5),
            (90.0, 0.0),
        ]
        for pct, mult in cases:
            line = resolve_dca_line(
                "沪深300",
                pct,
                drawdown_from_52w_high=0.10,
                policy=self.policy,
            )
            self.assertAlmostEqual(line["multiplier"], mult, places=3, msg=f"A:{pct}")
            us = resolve_dca_line(
                "标普500",
                pct,
                drawdown_from_52w_high=0.10,
                premium=0.0,
                verified=True,
                tradeable=True,
                policy=self.policy,
            )
            self.assertAlmostEqual(us["multiplier"], mult, places=3, msg=f"US:{pct}")

    def test_qdii_premium_none_fail_closed(self) -> None:
        line = resolve_dca_line(
            "标普500",
            30.0,
            premium=None,
            drawdown_from_52w_high=0.10,
            verified=True,
            tradeable=True,
            policy=self.policy,
        )
        self.assertTrue(line["paused"])
        self.assertEqual(line["action"], "premium_block")

    def test_portfolio_budget_keys(self) -> None:
        self.assertEqual(portfolio_monthly_base(self.policy), 300.0)
        self.assertEqual(portfolio_monthly_cap(self.policy), 1000.0)

    def test_current_snapshot_levels_are_half_band(self) -> None:
        line = resolve_dca_line(
            "沪深300",
            80.06,
            drawdown_from_52w_high=0.07,
            policy=self.policy,
        )
        self.assertAlmostEqual(line["multiplier"], 0.5)

    def test_qdii_premium_pauses_us_dca(self) -> None:
        line = resolve_dca_line(
            "标普500",
            40.0,
            premium=0.03,
            policy=self.policy,
            verified=True,
            tradeable=True,
        )
        self.assertTrue(line["paused"])
        self.assertEqual(line["action"], "premium_block")

    def test_missing_drawdown_blocks_boost(self) -> None:
        line = resolve_dca_line("沪深300", 25.0, policy=self.policy)
        self.assertAlmostEqual(line["multiplier"], 1.0)
        self.assertIn("缺回撤数据", line["reason"])

    def test_resolve_action_dca_only_no_build_merge(self) -> None:
        action, reason = resolve_action(
            "沪深300",
            80.0,
            percentile_1y=40.0,
            drawdown_from_52w_high=0.10,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertEqual(action, "half")
        self.assertIn("40", reason)  # 300 * 0.27 * 0.5 = 40.5 → "40"

    def test_near_high_stops_boost_above_base(self) -> None:
        line = resolve_dca_line(
            "沪深300",
            25.0,
            drawdown_from_52w_high=0.0,
            policy=self.policy,
        )
        self.assertAlmostEqual(line["multiplier"], 1.0)
        self.assertIn("停止加码", line["reason"])

    def test_build_independent(self) -> None:
        line = resolve_build_line(
            "沪深300",
            80.0,
            percentile_1y=45.0,
            drawdown_from_52w_high=0.06,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertTrue(line["active"])
        self.assertAlmostEqual(line["fraction"], 1.0)

    def test_ndx_excluded(self) -> None:
        line = resolve_dca_line("纳斯达克100", 10.0, policy=self.policy)
        self.assertEqual(line["action"], "reference")


class DcaAllocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()

    def test_five_sleeves_sum_at_half_band(self) -> None:
        equity = [
            resolve_dca_line("沪深300", 80.0, drawdown_from_52w_high=0.05, policy=self.policy),
            resolve_dca_line("中证500", 80.0, drawdown_from_52w_high=0.05, policy=self.policy),
            resolve_dca_line(
                "标普500",
                80.0,
                drawdown_from_52w_high=0.05,
                premium=0.0,
                verified=True,
                tradeable=True,
                policy=self.policy,
            ),
            resolve_dca_line("纳斯达克100", 10.0, policy=self.policy),
        ]
        today = date(2026, 6, 4)
        lines = allocate_dca_plan(equity, policy=self.policy, today=today, month_spent=0)
        by_name = {ln["name"]: ln for ln in lines}
        # 50% band: equity sleeves half; NDX→短债
        self.assertEqual(by_name["沪深300"]["monthly"], 40.5)  # 81*0.5
        self.assertEqual(by_name["中证500"]["monthly"], 16.5)
        self.assertEqual(by_name["标普500"]["monthly"], 12.0)
        self.assertEqual(by_name["纳斯达克100"]["monthly"], 0.0)
        self.assertEqual(by_name["短债"]["monthly"], 162.0)  # 153+9
        total = round(sum(ln["monthly"] for ln in lines), 2)
        self.assertEqual(total, 231.0)

    def test_triple_unlocks_portfolio_cap_1000(self) -> None:
        equity = [
            resolve_dca_line("沪深300", 25.0, drawdown_from_52w_high=0.10, policy=self.policy),
            resolve_dca_line("中证500", 25.0, drawdown_from_52w_high=0.10, policy=self.policy),
            resolve_dca_line(
                "标普500",
                30.0,
                drawdown_from_52w_high=0.10,
                premium=0.0,
                verified=True,
                tradeable=True,
                policy=self.policy,
            ),
            resolve_dca_line("纳斯达克100", 10.0, policy=self.policy),
        ]
        lines = allocate_dca_plan(
            equity, policy=self.policy, today=date(2026, 6, 4), month_spent=0
        )
        total = round(sum(ln["monthly"] for ln in lines), 2)
        self.assertEqual(total, 1000.0)
        # Unscaled weight×3 would only be ~576; scaling must unlock cap
        self.assertGreater(total, 576.0)

    def test_half_band_does_not_inflate_to_base(self) -> None:
        equity = [
            resolve_dca_line("沪深300", 80.0, drawdown_from_52w_high=0.05, policy=self.policy),
            resolve_dca_line("中证500", 80.0, drawdown_from_52w_high=0.05, policy=self.policy),
            resolve_dca_line(
                "标普500",
                80.0,
                drawdown_from_52w_high=0.05,
                premium=0.0,
                verified=True,
                tradeable=True,
                policy=self.policy,
            ),
            resolve_dca_line("纳斯达克100", 10.0, policy=self.policy),
        ]
        lines = allocate_dca_plan(
            equity, policy=self.policy, today=date(2026, 6, 4), month_spent=0
        )
        total = round(sum(ln["monthly"] for ln in lines), 2)
        self.assertEqual(total, 231.0)

        policy = load_policy()
        policy = {
            **policy,
            "dca": {**policy["dca"], "monthly_base": 300, "monthly_cap": 200},
        }
        equity = [
            resolve_dca_line("沪深300", 25.0, drawdown_from_52w_high=0.10, policy=policy),
            resolve_dca_line("中证500", 25.0, drawdown_from_52w_high=0.10, policy=policy),
            resolve_dca_line(
                "标普500",
                30.0,
                drawdown_from_52w_high=0.10,
                premium=0.0,
                verified=True,
                tradeable=True,
                policy=policy,
            ),
            resolve_dca_line("纳斯达克100", 10.0, policy=policy),
        ]
        lines = allocate_dca_plan(
            equity, policy=policy, today=date(2026, 6, 4), month_spent=0
        )
        total = round(sum(ln["monthly"] for ln in lines), 2)
        self.assertLessEqual(total, 200.0)
        by_name = {ln["name"]: ln for ln in lines}
        # Priority fills HS300 first
        self.assertGreater(by_name["沪深300"]["monthly"], by_name["短债"]["monthly"])

    def test_five_thursday_month_respects_remaining(self) -> None:
        self.assertEqual(len(thursdays_in_month(2026, 7)), 5)
        today = date(2026, 7, 2)  # first Thursday
        left = remaining_thursdays(today)
        self.assertEqual(len(left), 5)
        equity = [
            resolve_dca_line("沪深300", 65.0, drawdown_from_52w_high=0.05, policy=self.policy),
            resolve_dca_line("中证500", 65.0, drawdown_from_52w_high=0.05, policy=self.policy),
            resolve_dca_line(
                "标普500",
                65.0,
                drawdown_from_52w_high=0.05,
                premium=0.0,
                verified=True,
                tradeable=True,
                policy=self.policy,
            ),
            resolve_dca_line("纳斯达克100", 10.0, policy=self.policy),
        ]
        lines = allocate_dca_plan(equity, policy=self.policy, today=today, month_spent=0)
        week_sum = round(sum(ln["weekly"] for ln in lines), 2)
        self.assertEqual(week_sum, 60.0)  # 300 / 5

        # After 4 weeks planned (240), last Thursday only 60 left
        lines2 = allocate_dca_plan(
            equity, policy=self.policy, today=date(2026, 7, 30), month_spent=240.0
        )
        week_sum2 = round(sum(ln["weekly"] for ln in lines2), 2)
        self.assertEqual(week_sum2, 60.0)
        self.assertEqual(lines2[0]["month_remaining"], 60.0)

        # Over-spend guard: spent already at target → weekly 0
        lines3 = allocate_dca_plan(
            equity, policy=self.policy, today=date(2026, 7, 30), month_spent=300.0
        )
        self.assertEqual(round(sum(ln["weekly"] for ln in lines3), 2), 0.0)


if __name__ == "__main__":
    unittest.main()
