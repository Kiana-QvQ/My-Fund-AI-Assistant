"""Refresh US equity PE snapshot (SPY / QQQ trailing PE via yfinance).

Writes:
- config/us_pe_snapshot.json  (consumed by market snapshot / README / email)
- data/us_pe_history.json     (growing series for near-10y percentile)
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "config" / "us_pe_snapshot.json"
HISTORY_PATH = ROOT / "data" / "us_pe_history.json"
CST = timezone(timedelta(hours=8))

INDEXES = {
    "标普500": {"ticker": "SPY", "fallback_pe": 27.5, "fallback_percentile": 76.0},
    "纳斯达克100": {"ticker": "QQQ", "fallback_pe": 33.6, "fallback_percentile": 72.0},
}
WINDOW_DAYS = 365 * 10
MIN_POINTS_FOR_PERCENTILE = 60


def _today() -> str:
    return datetime.now(CST).date().isoformat()


def _load_json(path: Path, default: dict) -> dict:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def fetch_trailing_pe(ticker: str) -> float | None:
    info = yf.Ticker(ticker).info or {}
    value = info.get("trailingPE")
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def _percentile(series: list[float], current: float) -> float:
    if not series:
        return 50.0
    return sum(1 for item in series if item <= current) / len(series) * 100


def _window_series(points: list[dict], as_of: str) -> list[float]:
    cutoff = (
        datetime.strptime(as_of, "%Y-%m-%d").date() - timedelta(days=WINDOW_DAYS)
    ).isoformat()
    values = []
    for point in points:
        day = str(point.get("date", ""))[:10]
        pe = point.get("pe_ttm")
        if day >= cutoff and isinstance(pe, (int, float)):
            values.append(float(pe))
    return values


def refresh_us_pe(as_of: str | None = None, *, persist: bool = True) -> dict:
    as_of = as_of or _today()
    history = _load_json(
        HISTORY_PATH, {"as_of": as_of, "indexes": {name: [] for name in INDEXES}}
    )
    old_snapshot = _load_json(SNAPSHOT_PATH, {})
    indexes_out: dict[str, dict] = {}

    for name, meta in INDEXES.items():
        ticker = meta["ticker"]
        pe = None
        fetch_error = None
        try:
            pe = fetch_trailing_pe(ticker)
        except Exception as exc:  # network / rate limit
            fetch_error = str(exc)

        series = list(history.get("indexes", {}).get(name, []))
        if pe is None:
            # Keep last known PE if live fetch fails.
            if series:
                pe = float(series[-1]["pe_ttm"])
            else:
                pe = float(
                    old_snapshot.get("indexes", {})
                    .get(name, {})
                    .get("pe_ttm", meta["fallback_pe"])
                )
            status = "fallback_cached"
        else:
            status = "live"

        # Always record one point per day so percentile history can grow.
        if not series or series[-1].get("date") != as_of:
            series.append({"date": as_of, "pe_ttm": round(float(pe), 2)})
        else:
            series[-1] = {"date": as_of, "pe_ttm": round(float(pe), 2)}

        history.setdefault("indexes", {})[name] = series[-4000:]
        window_values = _window_series(series, as_of)
        if len(window_values) >= MIN_POINTS_FOR_PERCENTILE:
            percentile = round(_percentile(window_values, float(pe)), 2)
            percentile_source = f"history_n={len(window_values)}"
        else:
            percentile = float(
                old_snapshot.get("indexes", {})
                .get(name, {})
                .get("pe_percentile", meta["fallback_percentile"])
            )
            percentile_source = (
                f"reference_fallback(n={len(window_values)}<{MIN_POINTS_FOR_PERCENTILE})"
            )

        item = {
            "pe_ttm": round(float(pe), 2),
            "pe_percentile": percentile,
            "ticker": ticker,
            "status": status,
            "percentile_source": percentile_source,
            "history_points": len(series),
            "reason": (
                f"{ticker} trailing PE={float(pe):.2f}；"
                f"近10年分位来源 {percentile_source}"
            ),
        }
        if fetch_error:
            item["fetch_error"] = fetch_error
        indexes_out[name] = item

    snapshot = {
        "as_of": as_of,
        "window": "近10年滚动PE分位（历史样本不足时沿用参考分位）",
        "source": "yfinance trailingPE (SPY/QQQ) + local history",
        "updated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "indexes": indexes_out,
    }
    history["as_of"] = as_of
    if persist:
        _write_json(HISTORY_PATH, history)
        _write_json(SNAPSHOT_PATH, snapshot)
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="刷新美股 PE 快照")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    snapshot = refresh_us_pe(args.as_of, persist=not args.dry_run)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
