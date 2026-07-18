"""Tests for human-readable DCA change summaries."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from alert_state import format_dca_changes  # noqa: E402


class FormatDcaChangesTests(unittest.TestCase):
    def test_multiplier_change_is_readable(self) -> None:
        old = {
            "沪深300": {
                "multiplier": 1.0,
                "monthly": 81.0,
                "weekly": 40.5,
                "paused": False,
                "action": "buy",
            },
            "中证500": {
                "multiplier": 1.0,
                "monthly": 33.0,
                "weekly": 16.5,
                "paused": False,
                "action": "buy",
            },
        }
        new = {
            "沪深300": {
                "multiplier": 0.7,
                "monthly": 56.7,
                "weekly": 28.35,
                "paused": False,
                "action": "seventy",
            },
            "中证500": {
                "multiplier": 0.5,
                "monthly": 16.5,
                "weekly": 8.25,
                "paused": False,
                "action": "half",
            },
        }
        lines = format_dca_changes(old, new)
        text = "\n".join(lines)
        self.assertIn("沪深300：100% → 70%", text)
        self.assertIn("中证500：100% → 50%", text)
        self.assertNotIn("multiplier", text)
        self.assertNotIn("{", text)

    def test_premium_pause_label(self) -> None:
        old = {
            "标普500": {
                "multiplier": 1.0,
                "monthly": 24.0,
                "weekly": 6.0,
                "paused": False,
                "action": "buy",
            }
        }
        new = {
            "标普500": {
                "multiplier": 0.0,
                "monthly": 0.0,
                "weekly": 0.0,
                "paused": True,
                "action": "premium_block",
            }
        }
        lines = format_dca_changes(old, new)
        self.assertEqual(lines, ["标普500：100% → 溢价暂停"])


if __name__ == "__main__":
    unittest.main()
