"""Tests for personal trading-day DCA auto-ledger sync."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from policy_rules import load_policy  # noqa: E402
from sync_personal_dca import sync_personal_dca  # noqa: E402


class SyncPersonalDcaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()
        self.trading = {
            date(2026, 8, 7),
            date(2026, 8, 10),
            date(2026, 8, 11),
            date(2026, 8, 12),
            date(2026, 8, 13),
            date(2026, 8, 14),
        }

    def _is_trading(self, day: date) -> bool:
        return day in self.trading

    def test_backfills_gap_after_last_personal_dca(self) -> None:
        doc = {
            "holdings": [
                {
                    "fund_code": "016452",
                    "name": "南方纳斯达克100指数发起(QDII)A",
                    "cost_basis": 90.0,
                    "target_percent": 3.0,
                    "asset_class": "美股QDII",
                }
            ],
            "transactions": [
                {
                    "side": "buy",
                    "fund_code": "016452",
                    "trade_date": "2026-08-07",
                    "amount": 10.0,
                    "purpose": "dca",
                    "note": "纳斯达克100交易日定投",
                }
            ],
        }
        booked = sync_personal_dca(
            doc,
            as_of=date(2026, 8, 14),
            policy=self.policy,
            is_trading_day=self._is_trading,
        )
        self.assertEqual(len(booked), 5)
        self.assertEqual(
            [row["trade_date"] for row in booked],
            [
                "2026-08-10",
                "2026-08-11",
                "2026-08-12",
                "2026-08-13",
                "2026-08-14",
            ],
        )
        holding = next(h for h in doc["holdings"] if h["fund_code"] == "016452")
        self.assertEqual(holding["cost_basis"], 140.0)

    def test_skips_weekend_and_is_idempotent(self) -> None:
        doc = {
            "holdings": [
                {
                    "fund_code": "016452",
                    "cost_basis": 90.0,
                    "target_percent": 3.0,
                    "asset_class": "美股QDII",
                }
            ],
            "transactions": [
                {
                    "side": "buy",
                    "fund_code": "016452",
                    "trade_date": "2026-08-07",
                    "amount": 10.0,
                    "purpose": "dca",
                    "note": "纳斯达克100交易日定投",
                }
            ],
        }
        first = sync_personal_dca(
            doc,
            as_of=date(2026, 8, 11),
            policy=self.policy,
            is_trading_day=self._is_trading,
        )
        second = sync_personal_dca(
            doc,
            as_of=date(2026, 8, 11),
            policy=self.policy,
            is_trading_day=self._is_trading,
        )
        self.assertEqual([r["trade_date"] for r in first], ["2026-08-10", "2026-08-11"])
        self.assertEqual(second, [])
        holding = next(h for h in doc["holdings"] if h["fund_code"] == "016452")
        self.assertEqual(holding["cost_basis"], 110.0)

    def test_without_history_only_books_as_of(self) -> None:
        doc = {
            "holdings": [
                {
                    "fund_code": "016452",
                    "cost_basis": 80.0,
                    "target_percent": 3.0,
                    "asset_class": "美股QDII",
                }
            ],
            "transactions": [
                {
                    "side": "buy",
                    "fund_code": "016452",
                    "trade_date": "2026-08-03",
                    "amount": 10.0,
                    "purpose": "build",
                    "note": "纳斯达克100建仓",
                }
            ],
        }
        booked = sync_personal_dca(
            doc,
            as_of=date(2026, 8, 14),
            policy=self.policy,
            is_trading_day=self._is_trading,
        )
        self.assertEqual(len(booked), 1)
        self.assertEqual(booked[0]["trade_date"], "2026-08-14")

    def test_respects_monthly_stop(self) -> None:
        doc = {
            "holdings": [
                {
                    "fund_code": "016452",
                    "cost_basis": 215.0,
                    "target_percent": 3.0,
                    "asset_class": "美股QDII",
                }
            ],
            "transactions": [
                {
                    "side": "buy",
                    "fund_code": "016452",
                    "trade_date": "2026-08-07",
                    "amount": 215.0,
                    "purpose": "dca",
                    "note": "纳斯达克100交易日定投",
                }
            ],
        }
        booked = sync_personal_dca(
            doc,
            as_of=date(2026, 8, 14),
            policy=self.policy,
            is_trading_day=self._is_trading,
        )
        self.assertEqual(booked, [])


if __name__ == "__main__":
    unittest.main()
