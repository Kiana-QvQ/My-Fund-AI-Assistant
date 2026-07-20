"""SMTP mock tests and workflow step-plan simulation."""

from __future__ import annotations

import os
import smtplib
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import record_holding as ledger  # noqa: E402
import send_trade_alert_email as email_mod  # noqa: E402
import workflow_plan  # noqa: E402


CST = timezone(timedelta(hours=8))


class FakeSMTPSSL:
    """Context-manager SMTP that fails login (then optionally succeeds)."""

    fail_times: int = 99
    calls: int = 0

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> "FakeSMTPSSL":
        return self

    def __exit__(self, *args) -> bool:
        return False

    def login(self, *args, **kwargs) -> None:
        type(self).calls += 1
        if type(self).calls <= type(self).fail_times:
            raise smtplib.SMTPAuthenticationError(535, b"auth failed")

    def sendmail(self, *args, **kwargs) -> None:
        return None


class SmtpMockTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSMTPSSL.calls = 0
        FakeSMTPSSL.fail_times = 99

    def test_missing_mail_secrets_exit(self) -> None:
        env = {"PATH": os.environ.get("PATH", "")}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                email_mod.require_mail_config()
        self.assertIn("ALERT_EMAIL", str(ctx.exception))
        self.assertIn("SMTP_USER", str(ctx.exception))
        self.assertIn("SMTP_PASS", str(ctx.exception))

    def test_send_email_retries_and_writes_failure_summary(self) -> None:
        FakeSMTPSSL.fail_times = 99
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            env = {
                "ALERT_EMAIL": "user@example.com",
                "SMTP_USER": "user@example.com",
                "SMTP_PASS": "secret",
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PORT": "465",
                "GITHUB_STEP_SUMMARY": str(summary),
            }
            with patch.dict(os.environ, env, clear=False), patch(
                "smtplib.SMTP_SSL", FakeSMTPSSL
            ), patch("time.sleep"):
                with self.assertRaises(SystemExit) as ctx:
                    email_mod.send_email("主题", "正文", retries=2)
            self.assertIn("已重试 3 次", str(ctx.exception))
            self.assertEqual(FakeSMTPSSL.calls, 3)
            text = summary.read_text(encoding="utf-8")
            self.assertIn("邮件发送失败", text)
            self.assertIn("SMTP Secrets", text)

    def test_send_email_success_after_retry_writes_summary(self) -> None:
        FakeSMTPSSL.fail_times = 1  # first attempt fails, second succeeds
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            env = {
                "ALERT_EMAIL": "user@example.com",
                "SMTP_USER": "user@example.com",
                "SMTP_PASS": "secret",
                "SMTP_PORT": "465",
                "GITHUB_STEP_SUMMARY": str(summary),
            }
            with patch.dict(os.environ, env, clear=False), patch(
                "smtplib.SMTP_SSL", FakeSMTPSSL
            ), patch("time.sleep"):
                email_mod.send_email("主题OK", "正文", retries=2)
            self.assertEqual(FakeSMTPSSL.calls, 2)
            text = summary.read_text(encoding="utf-8")
            self.assertIn("邮件发送成功", text)
            self.assertIn("主题OK", text)


class WorkflowPlanSimulationTests(unittest.TestCase):
    def test_holiday_runs_us_readme_and_event_email(self) -> None:
        plan = workflow_plan.plan_portfolio_update_steps(
            run="no", run_us="yes", alert_mode="event"
        )
        self.assertFalse(plan["refresh_full"])
        self.assertTrue(plan["refresh_us_only"])
        self.assertTrue(plan["update_readme"])
        self.assertTrue(plan["commit"])
        self.assertTrue(plan["send_email"])
        self.assertEqual(plan["email_mode"], "event")
        self.assertTrue(plan["holiday_notice"])

    def test_trading_day_auto_can_email(self) -> None:
        plan = workflow_plan.plan_portfolio_update_steps(
            run="yes", run_us="yes", alert_mode="auto"
        )
        self.assertTrue(plan["refresh_full"])
        self.assertFalse(plan["refresh_us_only"])
        self.assertTrue(plan["send_email"])
        self.assertEqual(plan["email_mode"], "auto")

    def test_weekly_dca_mode(self) -> None:
        plan = workflow_plan.plan_portfolio_update_steps(
            run="yes", run_us="yes", alert_mode="weekly_dca"
        )
        self.assertTrue(plan["send_email"])
        self.assertEqual(plan["email_mode"], "weekly_dca")

    def test_skip_mode_blocks_email(self) -> None:
        plan = workflow_plan.plan_portfolio_update_steps(
            run="yes", run_us="yes", alert_mode="skip"
        )
        self.assertTrue(plan["refresh_full"])
        self.assertFalse(plan["send_email"])

    def test_yaml_conditions_match_planner(self) -> None:
        text = (ROOT / ".github" / "workflows" / "portfolio-update.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("python -m compileall -q app scripts tests", text)
        self.assertIn("Failure notice", text)
        self.assertIn("steps.gate.outputs.run != 'yes' && steps.gate.outputs.run_us == 'yes'", text)
        self.assertIn("--mode", text)
        self.assertIn("weekly_dca", text)
        self.assertIn("persist-state", text)
        self.assertIn("git rebase", text)
        self.assertIn('git push origin "HEAD:${BRANCH}"', text)
        self.assertNotIn("git push || true", text)
        holiday = workflow_plan.plan_portfolio_update_steps(
            run="no", run_us="yes", alert_mode="skip"
        )
        self.assertTrue(holiday["refresh_us_only"])
        self.assertTrue(holiday["update_readme"])
        self.assertFalse(holiday["send_email"])


class IdempotencyCstTests(unittest.TestCase):
    def test_idempotency_uses_cst_not_local_today(self) -> None:
        # Freeze CST to 2026-07-18 01:30 while local "today" would differ if UTC.
        fixed = datetime(2026, 7, 18, 1, 30, tzinfo=CST)
        doc = {"holdings": [], "transactions": []}
        with patch.object(ledger, "datetime") as mock_dt:
            mock_dt.now = MagicMock(return_value=fixed)
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            # today_cst uses datetime.now(CST)
            self.assertEqual(ledger.today_cst(), date(2026, 7, 18))
            ledger.apply_buy(doc, "460300", 100.0, "note-a")
            self.assertEqual(doc["transactions"][0]["trade_date"], "2026-07-18")
            # Same note same CST day -> duplicate
            with self.assertRaises(SystemExit):
                ledger.apply_buy(doc, "460300", 100.0, "note-a")
            # Different note -> allowed
            ledger.apply_buy(doc, "460300", 100.0, "note-b")
            self.assertEqual(len(doc["transactions"]), 2)


if __name__ == "__main__":
    unittest.main()
