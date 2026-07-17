"""Build state machine: confirm upgrades, immediate risk signals."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_state_machine import (  # noqa: E402
    STATE_DATA_FAIL,
    STATE_FORMAL_100,
    STATE_FORMAL_50,
    STATE_PREMIUM,
    STATE_SOFT_25,
    STATE_TAKE_PROFIT,
    STATE_UNBUYABLE,
    advance_machine,
    observe_build_state,
)
from policy_rules import load_policy  # noqa: E402


class AdvanceMachineTests(unittest.TestCase):
    def test_baseline_no_email(self) -> None:
        m, notify, change = advance_machine(None, STATE_SOFT_25, confirm_needed=2)
        self.assertFalse(notify)
        self.assertIsNone(change)
        self.assertEqual(m["current_state"], STATE_SOFT_25)
        self.assertEqual(m["last_notified_state"], STATE_SOFT_25)

    def test_upgrade_needs_two_days(self) -> None:
        base = {
            "current_state": STATE_SOFT_25,
            "candidate_state": None,
            "candidate_count": 0,
            "last_notified_state": STATE_SOFT_25,
            "last_notified_at": None,
        }
        m1, n1, _ = advance_machine(base, STATE_FORMAL_50, confirm_needed=2)
        self.assertFalse(n1)
        self.assertEqual(m1["current_state"], STATE_SOFT_25)
        self.assertEqual(m1["candidate_state"], STATE_FORMAL_50)
        self.assertEqual(m1["candidate_count"], 1)

        m2, n2, change = advance_machine(m1, STATE_FORMAL_50, confirm_needed=2)
        self.assertTrue(n2)
        self.assertEqual(m2["current_state"], STATE_FORMAL_50)
        self.assertIn("连续2日确认", change or "")

    def test_premium_immediate(self) -> None:
        base = {
            "current_state": STATE_SOFT_25,
            "candidate_state": None,
            "candidate_count": 0,
            "last_notified_state": STATE_SOFT_25,
            "last_notified_at": None,
        }
        m, notify, change = advance_machine(base, STATE_PREMIUM, confirm_needed=2)
        self.assertTrue(notify)
        self.assertEqual(m["current_state"], STATE_PREMIUM)
        self.assertIn("→", change or "")

    def test_lose_buyable_immediate(self) -> None:
        base = {
            "current_state": STATE_FORMAL_100,
            "candidate_state": None,
            "candidate_count": 0,
            "last_notified_state": STATE_FORMAL_100,
            "last_notified_at": None,
        }
        m, notify, _ = advance_machine(base, STATE_UNBUYABLE, confirm_needed=2)
        self.assertTrue(notify)
        self.assertEqual(m["current_state"], STATE_UNBUYABLE)

    def test_same_state_no_repeat(self) -> None:
        base = {
            "current_state": STATE_SOFT_25,
            "candidate_state": None,
            "candidate_count": 0,
            "last_notified_state": STATE_SOFT_25,
            "last_notified_at": None,
        }
        m, notify, _ = advance_machine(base, STATE_SOFT_25, confirm_needed=2)
        self.assertFalse(notify)
        self.assertEqual(m["current_state"], STATE_SOFT_25)

    def test_candidate_reset_on_flicker(self) -> None:
        base = {
            "current_state": STATE_UNBUYABLE,
            "candidate_state": STATE_SOFT_25,
            "candidate_count": 1,
            "last_notified_state": STATE_UNBUYABLE,
            "last_notified_at": None,
        }
        m, notify, _ = advance_machine(base, STATE_UNBUYABLE, confirm_needed=2)
        self.assertFalse(notify)
        self.assertIsNone(m["candidate_state"])
        self.assertEqual(m["candidate_count"], 0)


class ObserveStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()

    def test_soft_25(self) -> None:
        obs = observe_build_state(
            "沪深300",
            percentile=80.0,
            percentile_1y=85.0,
            drawdown_from_52w_high=0.07,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertEqual(obs["state"], STATE_SOFT_25)
        self.assertTrue(obs["active"])

    def test_formal_100(self) -> None:
        obs = observe_build_state(
            "沪深300",
            percentile=80.0,
            percentile_1y=45.0,
            drawdown_from_52w_high=0.06,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertEqual(obs["state"], STATE_FORMAL_100)

    def test_premium_block(self) -> None:
        obs = observe_build_state(
            "标普500",
            percentile=50.0,
            percentile_1y=40.0,
            drawdown_from_52w_high=0.10,
            premium=0.05,
            verified=True,
            tradeable=True,
            policy=self.policy,
            held_cost=0.0,
            target_amount=800.0,
        )
        self.assertEqual(obs["state"], STATE_PREMIUM)

    def test_take_profit(self) -> None:
        obs = observe_build_state(
            "沪深300",
            percentile=92.0,
            percentile_1y=40.0,
            drawdown_from_52w_high=0.10,
            policy=self.policy,
            held_cost=0.0,
            target_amount=2700.0,
        )
        self.assertEqual(obs["state"], STATE_TAKE_PROFIT)

    def test_data_fail_missing_1y(self) -> None:
        obs = observe_build_state(
            "沪深300",
            percentile=50.0,
            percentile_1y=None,
            drawdown_from_52w_high=0.10,
            policy=self.policy,
        )
        self.assertEqual(obs["state"], STATE_DATA_FAIL)


if __name__ == "__main__":
    unittest.main()
