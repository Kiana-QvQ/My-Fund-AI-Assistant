"""Tests for avg-cost metrics and soft buy scaling."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cost_basis_metrics import (  # noqa: E402
    apply_soft_buy_scale,
    avg_cost,
    soft_buy_scale,
    vs_avg_pct,
)
from fund_nav import estimate_shares, lookup_nav_on_or_before  # noqa: E402
from policy_rules import load_policy  # noqa: E402


class AvgCostHelpersTests(unittest.TestCase):
    def test_avg_and_vs_pct(self) -> None:
        self.assertEqual(avg_cost(100, 50), 2.0)
        self.assertEqual(vs_avg_pct(2.2, 2.0), 10.0)
        self.assertIsNone(avg_cost(100, None))

    def test_estimate_shares_with_fee(self) -> None:
        # 0.12% fee → net = amount / 1.0012
        shares = estimate_shares(10.0, 2.0, fee_percent=0.12)
        self.assertAlmostEqual(shares, 10.0 / 1.0012 / 2.0, places=4)

    def test_lookup_nav_on_or_before(self) -> None:
        series = {"2026-08-10": 1.1, "2026-08-12": 1.2}
        self.assertEqual(
            lookup_nav_on_or_before(series, "2026-08-12"),
            ("2026-08-12", 1.2),
        )
        self.assertEqual(
            lookup_nav_on_or_before(series, "2026-08-11"),
            ("2026-08-10", 1.1),
        )

    def test_soft_scale_ladder(self) -> None:
        policy = load_policy()
        scale0, reason0 = soft_buy_scale(5.0, policy=policy)
        self.assertEqual(scale0, 1.0)
        self.assertIsNone(reason0)

        scale_mid, reason_mid = soft_buy_scale(8.0, policy=policy)
        self.assertEqual(scale_mid, 1.0)
        # At exactly start, t=0 → scale 1.0; use 11.5 for mid
        scale_mid, reason_mid = soft_buy_scale(11.5, policy=policy)
        self.assertLess(scale_mid, 1.0)
        self.assertGreater(scale_mid, 0.5)
        self.assertIn("软降", reason_mid or "")

        scale_full, _ = soft_buy_scale(15.0, policy=policy)
        self.assertEqual(scale_full, 0.5)

    def test_personal_dca_excluded(self) -> None:
        amt, reason, scale = apply_soft_buy_scale(
            10.0,
            fund_code="016452",
            vs_avg_percent=20.0,
            policy=load_policy(),
            is_personal_dca=True,
        )
        self.assertEqual(amt, 10.0)
        self.assertEqual(scale, 1.0)
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
