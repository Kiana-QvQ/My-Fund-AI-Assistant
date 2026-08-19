"""Tests for App-style A-share index spot parsing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from index_spot import (  # noqa: E402
    attach_a_share_spots,
    parse_sina_spot_line,
)


class IndexSpotTests(unittest.TestCase):
    def test_parse_sina_hs300_line(self) -> None:
        line = 'var hq_str_s_sh000300="沪深300,4582.68,-3.03,-0.07,100,200";'
        parsed = parse_sina_spot_line(line)
        assert parsed is not None
        self.assertEqual(parsed["price_spot"], 4582.68)
        self.assertEqual(parsed["price_change"], -3.03)
        self.assertAlmostEqual(parsed["price_prev_close"], 4585.71, places=2)

    def test_attach_spots_uses_fetcher(self) -> None:
        indexes: dict = {"沪深300": {}, "中证500": {}}

        def fake_fetch(names: tuple[str, ...]) -> dict:
            return {
                "沪深300": {
                    "price_spot": 4582.68,
                    "price_change": -3.03,
                    "price_change_pct": -0.07,
                    "price_prev_close": 4585.71,
                    "source": "test",
                    "status": "ok",
                    "as_of": "2026-08-19",
                }
            }

        attach_a_share_spots(indexes, fetcher=fake_fetch)
        self.assertEqual(indexes["沪深300"]["price_spot"], 4582.68)
        self.assertEqual(indexes["沪深300"]["price_spot_status"], "ok")


if __name__ == "__main__":
    unittest.main()
