"""Integration tests for weekly_dca_due and send_trade_alert_email.main()."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import send_trade_alert_email as email_mod  # noqa: E402
from alert_state import save_alert_state  # noqa: E402


class WeeklyDcaDueTests(unittest.TestCase):
    def test_thursday_always_due(self) -> None:
        self.assertTrue(email_mod.weekly_dca_due(date(2026, 7, 16)))  # Thu

    def test_friday_not_due_if_thursday_was_trading(self) -> None:
        with patch.object(email_mod, "is_a_share_trading_day", return_value=True):
            self.assertFalse(email_mod.weekly_dca_due(date(2026, 7, 17)))  # Fri

    def test_friday_due_when_thursday_was_holiday(self) -> None:
        # Thursday holiday → first following trading day (Friday) is due
        def fake_is_trading(day):
            d = day if isinstance(day, date) else date.fromisoformat(str(day))
            return d != date(2026, 7, 16)

        with patch.object(email_mod, "is_a_share_trading_day", side_effect=fake_is_trading), patch.object(
            email_mod,
            "next_a_share_trading_day",
            return_value=date(2026, 7, 17),
        ):
            self.assertTrue(email_mod.weekly_dca_due(date(2026, 7, 17)))


class EmailMainIntegrationTests(unittest.TestCase):
    def _mini_snapshot(self) -> dict:
        return {
            "as_of": "2026-07-16",
            "indexes": {
                "沪深300": {
                    "pe_percentile": 80.0,
                    "pe_percentile_1y": 72.0,
                    "drawdown_from_52w_high": 0.07,
                    "drawdown_from_52w_high_pct": 7.0,
                    "pe_ttm": 13.5,
                    "verified": True,
                    "tradeable": True,
                },
                "中证500": {
                    "pe_percentile": 85.0,
                    "pe_percentile_1y": 85.0,
                    "drawdown_from_52w_high": 0.11,
                    "drawdown_from_52w_high_pct": 11.0,
                    "pe_ttm": 30.0,
                    "verified": True,
                    "tradeable": True,
                },
                "标普500": {
                    "pe_percentile": 93.0,
                    "pe_percentile_1y": 100.0,
                    "qdii_premium": 0.05,
                    "verified": True,
                    "tradeable": True,
                },
                "纳斯达克100": {
                    "pe_percentile": None,
                    "qdii_premium": 0.06,
                    "verified": False,
                    "tradeable": False,
                    "reference_only": True,
                },
            },
        }

    def test_main_sends_weekly_and_event_on_thursday_with_changes(self) -> None:
        snap = self._mini_snapshot()
        sent: list[str] = []

        def fake_send(subject: str, body: str, dry_run: bool = False, **kwargs) -> None:
            sent.append(subject)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            snap_path = tmp_path / "snap.json"
            state_path = tmp_path / "alert_state.json"
            snap_path.write_text(json.dumps(snap), encoding="utf-8")
            # Seed old fingerprints different from current → event fires
            save_alert_state(
                {
                    "dca": {
                        "沪深300": {
                            "multiplier": 1.0,
                            "monthly": 81.0,
                            "weekly": 20.0,
                            "paused": False,
                            "action": "buy",
                        }
                    },
                    "build": {},
                    "dca_month": {"year_month": "2026-07", "emails_sent": 0, "spent": 0},
                },
                path=state_path,
            )

            with patch.object(email_mod, "SNAPSHOT_PATH", snap_path), patch.object(
                email_mod, "send_email", side_effect=fake_send
            ), patch(
                "alert_state.STATE_PATH", state_path
            ), patch.object(
                email_mod, "load_alert_state", lambda: json.loads(state_path.read_text(encoding="utf-8"))
            ), patch.object(
                email_mod,
                "save_alert_state",
                lambda state: state_path.write_text(
                    json.dumps(state, ensure_ascii=False), encoding="utf-8"
                ),
            ), patch.object(
                email_mod, "today_cst", return_value=date(2026, 7, 16)
            ), patch.object(
                email_mod, "weekly_dca_due", return_value=True
            ), patch.object(
                email_mod, "actual_dca_spent", return_value=0.0
            ), patch(
                "sys.argv",
                ["send_trade_alert_email.py", "--snapshot", str(snap_path), "--mode", "auto", "--dry-run"],
            ):
                # dry-run still calls send_email in this module
                email_mod.main()

        self.assertGreaterEqual(len(sent), 1)
        self.assertTrue(any("定投计划" in s and "周报" in s for s in sent))
        # With fingerprint change, event mail should also appear
        self.assertTrue(any("变更" in s for s in sent))

    def test_main_force_build_sends_build_mail(self) -> None:
        snap = self._mini_snapshot()
        sent: list[str] = []

        def fake_send(subject: str, body: str, dry_run: bool = False, **kwargs) -> None:
            sent.append(subject)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            snap_path = tmp_path / "snap.json"
            snap_path.write_text(json.dumps(snap), encoding="utf-8")
            with patch.object(email_mod, "send_email", side_effect=fake_send), patch.object(
                email_mod, "today_cst", return_value=date(2026, 7, 15)
            ), patch.object(
                email_mod, "weekly_dca_due", return_value=False
            ), patch.object(
                email_mod, "actual_dca_spent", return_value=0.0
            ), patch.object(
                email_mod,
                "load_alert_state",
                return_value={"dca": {"x": 1}, "build": {}, "dca_month": {}},
            ), patch(
                "sys.argv",
                [
                    "send_trade_alert_email.py",
                    "--snapshot",
                    str(snap_path),
                    "--mode",
                    "force_build",
                    "--dry-run",
                ],
            ):
                email_mod.main()

        self.assertTrue(any("建仓事件" in s for s in sent))


if __name__ == "__main__":
    unittest.main()
