"""Fetch and cache open-fund unit NAV history (Eastmoney lsjz API)."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "data" / "fund_nav_history.json"
USER_AGENT = (
    "Mozilla/5.0 (compatible; MyFundAIAssistant/1.0; +https://github.com/Kiana-QvQ)"
)

# Equity sleeves that use holding average cost for display / soft buy gates.
EQUITY_COST_FUNDS = ("460300", "160119", "016452")


def _load_cache() -> dict[str, Any]:
    if not CACHE_PATH.is_file():
        return {"funds": {}}
    try:
        doc = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"funds": {}}
    doc.setdefault("funds", {})
    return doc


def _save_cache(doc: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _parse_payload(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace").strip()
    if text.startswith("jQuery") or text.startswith("callback"):
        match = re.search(r"\((\{.*\})\)\s*;?\s*$", text, flags=re.S)
        if not match:
            raise RuntimeError("净值接口 JSONP 解析失败")
        text = match.group(1)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError("净值接口返回非对象")
    return data


def fetch_nav_history(
    fund_code: str,
    *,
    start: date | str,
    end: date | str,
    page_size: int = 40,
    sleep_s: float = 0.35,
) -> dict[str, float]:
    """Return {YYYY-MM-DD: unit_nav} for inclusive date range (network)."""
    code = str(fund_code)
    start_s = str(start)[:10]
    end_s = str(end)[:10]
    out: dict[str, float] = {}
    page = 1
    while True:
        params = urllib.parse.urlencode(
            {
                "callback": "jQuery",
                "fundCode": code,
                "pageIndex": page,
                "pageSize": page_size,
                "startDate": start_s,
                "endDate": end_s,
                "_": int(time.time() * 1000),
            }
        )
        url = f"https://api.fund.eastmoney.com/f10/lsjz?{params}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://fundf10.eastmoney.com/",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                payload = _parse_payload(resp.read())
        except urllib.error.URLError as exc:
            raise RuntimeError(f"拉取 {code} 净值失败: {exc}") from exc

        block = payload.get("Data") or {}
        rows = block.get("LSJZList") or []
        if not rows:
            break
        for row in rows:
            day = str(row.get("FSRQ") or "")[:10]
            nav_raw = row.get("DWJZ")
            if not day or nav_raw in (None, ""):
                continue
            out[day] = float(nav_raw)
        total = int(payload.get("TotalCount") or block.get("TotalCount") or 0)
        if page * page_size >= total or len(rows) < page_size:
            break
        page += 1
        time.sleep(sleep_s)
    return out


def merge_nav_cache(
    fund_code: str,
    points: dict[str, float],
    *,
    persist: bool = True,
) -> dict[str, float]:
    doc = _load_cache()
    bucket = doc["funds"].setdefault(str(fund_code), {})
    series = dict(bucket.get("by_date") or {})
    for day, nav in points.items():
        series[str(day)[:10]] = round(float(nav), 6)
    bucket["by_date"] = dict(sorted(series.items()))
    bucket["updated_at"] = datetime.now().isoformat(timespec="seconds")
    if persist:
        _save_cache(doc)
    return dict(bucket["by_date"])


def cached_nav_series(fund_code: str) -> dict[str, float]:
    doc = _load_cache()
    bucket = (doc.get("funds") or {}).get(str(fund_code)) or {}
    series = bucket.get("by_date") or {}
    return {str(k)[:10]: float(v) for k, v in series.items()}


def ensure_nav_range(
    fund_code: str,
    *,
    start: date | str,
    end: date | str,
    refresh: bool = False,
) -> dict[str, float]:
    """Ensure cache covers [start, end]; fetch missing span from network."""
    start_d = datetime.strptime(str(start)[:10], "%Y-%m-%d").date()
    end_d = datetime.strptime(str(end)[:10], "%Y-%m-%d").date()
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    series = cached_nav_series(fund_code)
    if refresh or not series:
        fetched = fetch_nav_history(fund_code, start=start_d, end=end_d)
        return merge_nav_cache(fund_code, fetched)

    # Expand a few days around gaps / ends.
    need_start = start_d - timedelta(days=5)
    need_end = end_d + timedelta(days=2)
    dates = sorted(series)
    have_start = datetime.strptime(dates[0], "%Y-%m-%d").date()
    have_end = datetime.strptime(dates[-1], "%Y-%m-%d").date()
    if have_start <= need_start and have_end >= need_end:
        return series
    fetch_from = min(need_start, have_start)
    fetch_to = max(need_end, have_end)
    fetched = fetch_nav_history(fund_code, start=fetch_from, end=fetch_to)
    return merge_nav_cache(fund_code, fetched)


def lookup_nav_on_or_before(
    series: dict[str, float],
    day: date | str,
    *,
    max_lookback_days: int = 10,
) -> tuple[str, float] | None:
    """Pick NAV on trade date, else nearest prior published NAV within lookback."""
    target = datetime.strptime(str(day)[:10], "%Y-%m-%d").date()
    for offset in range(0, max_lookback_days + 1):
        candidate = (target - timedelta(days=offset)).isoformat()
        if candidate in series:
            return candidate, float(series[candidate])
    return None


def estimate_shares(
    amount: float,
    nav: float,
    *,
    fee_percent: float | None = None,
) -> float:
    """Estimate confirmed shares: 净申购额 / 净值.

    fee_percent is percent points (e.g. 0.12 means 0.12%).
    """
    amt = float(amount)
    unit = float(nav)
    if amt <= 0 or unit <= 0:
        raise ValueError("金额与净值必须 > 0")
    fee_rate = max(0.0, float(fee_percent or 0.0) / 100.0)
    net = amt / (1.0 + fee_rate) if fee_rate > 0 else amt
    return round(net / unit, 4)


def fee_percent_from_snapshot(snapshot: dict | None, fund_code: str) -> float | None:
    if not snapshot:
        return None
    fund = (snapshot.get("funds") or {}).get(str(fund_code)) or {}
    raw = fund.get("fee_percent")
    if isinstance(raw, (int, float)):
        return float(raw)
    return None
