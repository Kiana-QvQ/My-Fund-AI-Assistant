"""Regression tests for the 2026-10-15 招行 App 截图对账.

招行 App 不提供逐笔明细，所以用 `成本 = 市值 − 持仓收益` 反推总成本。这些测试把
截图上的数字钉死，避免以后有人改账本时让「已投入」和 App 悄悄对不上。

对账基线是 **origin/main** 的账本（工作流已按交易日自动补记纳指每日定投，
截至 2026-10-14 合计 ¥4,250），而不是某个更旧的本地快照。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from record_holding import load_holdings  # noqa: E402

# 2026-10-15 招商银行 App 截图（尾号 7289）
APP_TOTAL_MARKET_VALUE = 4299.52
APP_TOTAL_HOLDING_PROFIT = -115.48
APP_TOTAL_COST = 4415.0  # = 4299.52 − (−115.48)，等于用户确认的「我投入的金额」
TODAY_DCA_TOTAL = 25.0  # 短债5 + 沪深300 5 + 中证500 5 + 纳指100 10
TODAY_DCA = {"012773": 5.0, "460300": 5.0, "160119": 5.0, "016452": 10.0}
RECONCILE_DAY = "2026-10-15"
# origin/main 账本截至 2026-10-14 合计（2000 + 920 + 820 + 510）
PRE_RECONCILE_LEDGER = 4250.0
# +200 未入账建仓（460300），−60 自动记账超出 App 口径的冲回（016452）
RECONCILE_DELTA = 140.0


def broker_snapshots(doc: dict) -> list[dict]:
    return [row["broker_snapshot"] for row in doc.get("holdings") or []]


def reconcile_txs(doc: dict) -> list[dict]:
    return [
        tx
        for tx in doc.get("transactions") or []
        if str(tx.get("transaction_id") or "").startswith("reconcile-")
    ]


class ScreenshotReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = load_holdings()

    def test_app_totals_match_the_screenshot(self) -> None:
        snaps = broker_snapshots(self.doc)
        self.assertEqual(len(snaps), 4)
        self.assertEqual(
            round(sum(s["market_value"] for s in snaps), 2), APP_TOTAL_MARKET_VALUE
        )
        self.assertEqual(
            round(sum(s["holding_profit"] for s in snaps), 2), APP_TOTAL_HOLDING_PROFIT
        )
        self.assertEqual(round(sum(s["implied_cost"] for s in snaps), 2), APP_TOTAL_COST)

    def test_each_snapshot_cost_closes(self) -> None:
        for snap in broker_snapshots(self.doc):
            expected = round(
                float(snap["market_value"]) - float(snap["holding_profit"]), 2
            )
            self.assertEqual(float(snap["implied_cost"]), expected)

    def test_reconcile_delta_bridges_ledger_to_app_total(self) -> None:
        """对账算术：origin/main 基线 4250 + 净补记 140 + 当日 25 = 4415。

        用常量而非实时账本合计，因为账本每个交易日都会继续增长（每日定投），
        实时合计不可能长期等于 4415。
        """
        total = round(sum(float(tx["cost_delta"]) for tx in reconcile_txs(self.doc)), 2)
        self.assertEqual(total, RECONCILE_DELTA)
        self.assertEqual(
            PRE_RECONCILE_LEDGER + total + TODAY_DCA_TOTAL, APP_TOTAL_COST
        )

    def test_ledger_total_equals_app_implied_cost_on_reconcile_day(self) -> None:
        """对账当天账本合计必须等于 App 反推成本；过了那天自动跳过。"""
        if str(self.doc.get("as_of")) != RECONCILE_DAY:
            self.skipTest(f"账本 as_of={self.doc.get('as_of')}，对账日已过，按设计跳过")
        ledger_cost = round(
            sum(float(row.get("cost_basis") or 0) for row in self.doc["holdings"]), 2
        )
        self.assertEqual(ledger_cost, APP_TOTAL_COST)

    def test_today_dca_entries_match_the_bank_schedule(self) -> None:
        """2026-10-15 的四笔定投已入账，且 note 与 sync_personal_dca 保持一致（幂等）。"""
        found: dict[str, float] = {}
        for tx in self.doc.get("transactions") or []:
            if str(tx.get("trade_date")) != RECONCILE_DAY:
                continue
            if str(tx.get("purpose") or "").lower() != "dca":
                continue
            found[str(tx["fund_code"])] = float(tx["amount"])
        self.assertEqual(found, TODAY_DCA)
        self.assertEqual(sum(found.values()), TODAY_DCA_TOTAL)

    def test_reconcile_transactions_are_present_and_auditable(self) -> None:
        by_id = {
            str(tx.get("transaction_id")): tx
            for tx in self.doc.get("transactions") or []
        }
        hs300 = by_id.get("reconcile-460300-20261014")
        ndx = by_id.get("reconcile-016452-20261014")
        self.assertIsNotNone(hs300, "沪深300 的 200 元未入账建仓补记缺失")
        self.assertIsNotNone(ndx, "纳指100 的 60 元超记冲回缺失")
        self.assertEqual(float(hs300["cost_delta"]), 200.0)
        self.assertEqual(float(ndx["cost_delta"]), -60.0)
        for tx in (hs300, ndx):
            self.assertEqual(tx["trade_date"], "2026-10-14")

    def test_writeoff_is_not_a_buy(self) -> None:
        """冲回不能是 buy：否则 backfill 会给它算份额，也会被当成定投流水。"""
        by_id = {
            str(tx.get("transaction_id")): tx
            for tx in self.doc.get("transactions") or []
        }
        ndx = by_id["reconcile-016452-20261014"]
        self.assertNotEqual(str(ndx.get("side")), "buy")
        self.assertLessEqual(float(ndx.get("amount") or 0), 0.0)

    def test_reconcile_notes_do_not_look_like_personal_dca(self) -> None:
        """对账流水不能被当成个人定投，否则会污染「本月已记」与补记起点。"""
        for tx in reconcile_txs(self.doc):
            self.assertNotEqual(str(tx.get("purpose") or "").lower(), "dca")
            self.assertNotIn("交易日定投", str(tx.get("note") or ""))

    def test_reconciled_holdings_have_no_stale_shares(self) -> None:
        """改成本后绝不能留旧份额 —— 否则均价（恒为 成本÷份额）会算错。

        对账时份额先被置空，再由 backfill_equity_shares.py 用历史净值重建；
        所以这里断言的是**最终不变量**，不绑定「是否已重建」这个中间状态。
        """
        for row in self.doc["holdings"]:
            code = str(row["fund_code"])
            shares = row.get("shares")
            if shares is None:
                # 仍是待重建占位：必须没有均价，且标了来源
                self.assertIsNone(row.get("avg_cost"), msg=code)
                if code != "012773":  # 短债本来就不记份额
                    self.assertEqual(
                        row.get("shares_source"), "pending_backfill", msg=code
                    )
                continue

            # 已重建：份额必须等于其买入流水份额之和
            total = round(
                sum(
                    float(tx["shares"])
                    for tx in self.doc.get("transactions") or []
                    if tx.get("side") == "buy"
                    and str(tx.get("fund_code")) == code
                    and isinstance(tx.get("shares"), (int, float))
                ),
                4,
            )
            self.assertGreater(float(shares), 0.0, msg=code)
            self.assertAlmostEqual(float(shares), total, places=4, msg=code)
            # 均价必须与成本/份额自洽
            self.assertAlmostEqual(
                float(row["avg_cost"]),
                round(float(row["cost_basis"]) / float(shares), 6),
                places=6,
                msg=code,
            )


if __name__ == "__main__":
    unittest.main()
