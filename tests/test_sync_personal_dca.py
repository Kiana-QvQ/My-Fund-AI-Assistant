"""Tests for personal trading-day DCA auto-ledger sync.

自 2026-10-15 起，四只持仓标的（012773 / 460300 / 160119 / 016452）全部改为
招商银行 App「每交易日」自动定投，且**不设月终止金额**。这些测试固定该口径，
并用 `effective_from` / `--since` 覆盖「不要补出无法核对的历史流水」这一点。
"""

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
from investment_plan import personal_dca_schedules  # noqa: E402


def single_schedule_policy(code: str = "016452", **overrides) -> dict:
    """Policy restricted to one schedule.

    The production policy carries four schedules; narrowing keeps each assertion
    about a single fund instead of re-deriving the other three every time. The
    production reconciliation boundary (``effective_from`` / ``reconciled_through``)
    is dropped so historical fixtures stay usable — tests that exercise a
    boundary re-add it through ``overrides``.
    """
    policy = load_policy()
    dca = dict(policy["dca"])
    schedule = dict((dca.get("personal_schedules") or {})[code])
    schedule.pop("effective_from", None)
    schedule.pop("reconciled_through", None)
    schedule.update(overrides)
    dca["personal_schedules"] = {code: schedule}
    return {**policy, "dca": dca}


def nasdaq_doc(last_date: str, amount: float = 10.0) -> dict:
    return {
        "holdings": [
            {
                "fund_code": "016452",
                "name": "南方纳斯达克100指数发起(QDII)A",
                "cost_basis": amount,
                "target_percent": 3.0,
                "asset_class": "美股QDII",
            }
        ],
        "transactions": [
            {
                "side": "buy",
                "fund_code": "016452",
                "trade_date": last_date,
                "amount": amount,
                "purpose": "dca",
                "note": "纳斯达克100交易日定投",
            }
        ],
    }


class SyncPersonalDcaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = single_schedule_policy("016452")
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
        doc = nasdaq_doc("2026-08-07")
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
        self.assertTrue(all(row["purpose"] == "dca" for row in booked))
        holding = next(h for h in doc["holdings"] if h["fund_code"] == "016452")
        self.assertEqual(holding["cost_basis"], 60.0)

    def test_skips_weekend_and_is_idempotent(self) -> None:
        doc = nasdaq_doc("2026-08-07", amount=90.0)
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

    def test_unlimited_schedule_keeps_buying_after_215(self) -> None:
        """无月终止金额：当月累计到 215 元后仍按交易日继续扣款。"""
        doc = nasdaq_doc("2026-08-07", amount=215.0)
        booked = sync_personal_dca(
            doc,
            as_of=date(2026, 8, 14),
            policy=self.policy,
            is_trading_day=self._is_trading,
        )
        self.assertEqual(len(booked), 5)
        holding = next(h for h in doc["holdings"] if h["fund_code"] == "016452")
        self.assertEqual(holding["cost_basis"], 265.0)

    def test_monthly_stop_is_honoured_when_configured(self) -> None:
        policy = single_schedule_policy("016452", monthly_stop_amount=215)
        doc = nasdaq_doc("2026-08-07", amount=215.0)
        booked = sync_personal_dca(
            doc,
            as_of=date(2026, 8, 14),
            policy=policy,
            is_trading_day=self._is_trading,
        )
        self.assertEqual(booked, [])


class EffectiveFromTests(unittest.TestCase):
    """effective_from 之前的日期不会被补记。"""

    def _is_trading(self, day: date) -> bool:
        return day.weekday() < 5

    def test_effective_from_blocks_earlier_backfill(self) -> None:
        policy = single_schedule_policy("016452", effective_from="2026-10-15")
        doc = nasdaq_doc("2026-08-19", amount=170.0)
        booked = sync_personal_dca(
            doc,
            as_of=date(2026, 10, 18),
            policy=policy,
            is_trading_day=self._is_trading,
        )
        # 10-15 Thu / 10-16 Fri；10-17、10-18 是周末
        self.assertEqual(
            [r["trade_date"] for r in booked], ["2026-10-15", "2026-10-16"]
        )

    def test_since_overrides_effective_from(self) -> None:
        policy = single_schedule_policy("016452", effective_from="2026-10-15")
        doc = nasdaq_doc("2026-08-19", amount=170.0)
        booked = sync_personal_dca(
            doc,
            as_of=date(2026, 8, 21),
            policy=policy,
            is_trading_day=self._is_trading,
            since=date(2026, 8, 20),
        )
        self.assertEqual(
            [r["trade_date"] for r in booked], ["2026-08-20", "2026-08-21"]
        )

    def test_fund_filter_restricts_run(self) -> None:
        doc = {"holdings": [], "transactions": []}
        booked = sync_personal_dca(
            doc,
            as_of=date(2026, 10, 15),
            policy=load_policy(),
            is_trading_day=self._is_trading,
            fund_codes={"460300"},
        )
        self.assertEqual({r["fund_code"] for r in booked}, {"460300"})
        self.assertEqual([r["amount"] for r in booked], [5.0])

    def test_all_four_schedules_book_on_one_trading_day(self) -> None:
        doc = {"holdings": [], "transactions": []}
        booked = sync_personal_dca(
            doc,
            as_of=date(2026, 10, 15),
            policy=load_policy(),
            is_trading_day=self._is_trading,
        )
        self.assertEqual(
            {r["fund_code"]: r["amount"] for r in booked},
            {"012773": 5.0, "460300": 5.0, "160119": 5.0, "016452": 10.0},
        )
        # 每日合计 25 元，且不计入组合周度预算
        self.assertEqual(sum(r["amount"] for r in booked), 25.0)

    def test_effective_from_before_as_of_does_not_backfill_future(self) -> None:
        policy = single_schedule_policy("016452", effective_from="2026-12-01")
        doc = nasdaq_doc("2026-08-19", amount=170.0)
        booked = sync_personal_dca(
            doc,
            as_of=date(2026, 10, 18),
            policy=policy,
            is_trading_day=self._is_trading,
        )
        self.assertEqual(booked, [])

    def test_reconciled_through_derives_effective_from(self) -> None:
        """只给 reconciled_through 时，自动记账从「次日」开始。"""
        policy = single_schedule_policy("016452", reconciled_through="2026-10-14")
        doc = nasdaq_doc("2026-08-19", amount=450.0)
        booked = sync_personal_dca(
            doc,
            as_of=date(2026, 10, 18),
            policy=policy,
            is_trading_day=self._is_trading,
        )
        # reconciled_through 10-14 → 从 10-15 起；10-15 周四、10-16 周五
        self.assertEqual(
            [r["trade_date"] for r in booked], ["2026-10-15", "2026-10-16"]
        )

    def test_effective_from_wins_over_reconciled_through(self) -> None:
        policy = single_schedule_policy(
            "016452",
            effective_from="2026-10-16",
            reconciled_through="2026-10-01",
        )
        doc = nasdaq_doc("2026-08-19", amount=450.0)
        booked = sync_personal_dca(
            doc,
            as_of=date(2026, 10, 18),
            policy=policy,
            is_trading_day=self._is_trading,
        )
        self.assertEqual([r["trade_date"] for r in booked], ["2026-10-16"])

    def test_real_policy_boundary_is_2026_10_15(self) -> None:
        """生产政策：10-14 及以前已对账，自动记账从 10-15 开始。"""
        policy = load_policy()
        for code, schedule in personal_dca_schedules(policy).items():
            self.assertEqual(
                schedule.get("reconciled_through"), "2026-10-14", msg=code
            )
            self.assertEqual(schedule.get("effective_from"), "2026-10-15", msg=code)


if __name__ == "__main__":
    unittest.main()
