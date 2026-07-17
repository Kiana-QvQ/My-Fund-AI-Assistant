"""S&P 500 index PE from Multpl + local 10y percentile; Nasdaq 100 stays unverified.

Pipeline:
1. Load/refresh Multpl monthly PE table → persist cache (skip full re-fetch if fresh).
2. Separately scrape Multpl homepage for the latest PE + timestamp.
3. Compute percentile vs last 10 years of monthly samples.
4. Four-layer validation (non-null / freshness / PE range / percentile range).
5. Fail-closed: parse/fetch failure → verified=False; never trade on expired cache.

Hard rules:
- Never use yfinance ETF trailingPE for trade decisions.
- Nasdaq 100: always unverified, never buy.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "config" / "us_pe_snapshot.json"
SPX_CACHE_PATH = ROOT / "data" / "sp500_pe_multpl.json"
CST = timezone(timedelta(hours=8))

MULTPL_MONTHLY_URL = "https://www.multpl.com/s-p-500-pe-ratio/table/by-month"
MULTPL_HOME_URL = "https://www.multpl.com/s-p-500-pe-ratio"

# Skip full monthly re-scrape when cache is newer than this.
MONTHLY_CACHE_MAX_AGE_DAYS = 7
# Monthly Multpl + near-real-time current: allow ~1.5 months lag for valuation date.
MAX_VALUATION_AGE_DAYS = 45
# Bounds catch scrape garbage; keep room for real high-PE regimes (take-profit).
SPX_PE_MIN = 5.0
SPX_PE_MAX = 80.0
WINDOW_YEARS = 10
MIN_HISTORY_POINTS = 60

USER_AGENT = (
    "Mozilla/5.0 (compatible; MyFundAIAssistant/1.0; "
    "+https://github.com/Kiana-QvQ/My-Fund-AI-Assistant)"
)


class _MultplTableParser(HTMLParser):
    """Extract (date_text, value_text) pairs from Multpl HTML tables."""

    def __init__(self) -> None:
        super().__init__()
        self.in_td = False
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.current_row = []
        elif tag == "td":
            self.in_td = True
            self._buf = ""

    def handle_endtag(self, tag):
        if tag == "td" and self.in_td:
            self.current_row.append(self._buf.strip())
            self.in_td = False
            self._buf = ""
        elif tag == "tr" and len(self.current_row) >= 2:
            self.rows.append(self.current_row[:2])
            self.current_row = []

    def handle_data(self, data):
        if self.in_td:
            self._buf += data


def _today() -> date:
    return datetime.now(CST).date()


def _load_json(path: Path, default: dict) -> dict:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _http_get(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _parse_multpl_date(text: str) -> date | None:
    cleaned = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    cleaned = cleaned.lstrip("†").strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %Y", "%B %Y"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            if fmt in ("%b %Y", "%B %Y"):
                return date(parsed.year, parsed.month, 1)
            return parsed.date()
        except ValueError:
            continue
    return None


def _parse_multpl_pe(text: str) -> float | None:
    cleaned = text.replace("\xa0", " ").replace(",", "").strip()
    cleaned = cleaned.lstrip("†").strip()
    # Strip HTML entities like &#x2002;
    cleaned = re.sub(r"&#x[0-9a-fA-F]+;", " ", cleaned)
    cleaned = re.sub(r"&#\d+;", " ", cleaned)
    match = re.search(r"(-?\d+(?:\.\d+)?)", cleaned)
    if not match:
        return None
    value = float(match.group(1))
    return value if value > 0 else None


def parse_multpl_monthly_html(html: str) -> list[dict]:
    parser = _MultplTableParser()
    parser.feed(html)
    points: list[dict] = []
    seen: set[str] = set()
    for row in parser.rows:
        day = _parse_multpl_date(row[0])
        pe = _parse_multpl_pe(row[1])
        if day is None or pe is None:
            continue
        key = day.isoformat()
        if key in seen:
            continue
        seen.add(key)
        points.append({"date": key, "pe_ttm": round(pe, 2)})
    points.sort(key=lambda item: item["date"])
    if len(points) < MIN_HISTORY_POINTS:
        raise RuntimeError(
            f"Multpl 月度表解析结果过少（{len(points)} 点），疑似页面结构变更"
        )
    return points


def parse_multpl_current_html(html: str, *, today: date | None = None) -> dict:
    """Parse latest PE and valuation date from Multpl homepage."""
    today = today or _today()
    pe: float | None = None

    current_block = re.search(
        r'id="current"[^>]*>(.*?)</div>\s*<table',
        html,
        re.I | re.S,
    )
    block = current_block.group(1) if current_block else html

    m = re.search(
        r"Current\s*(?:<[^>]+>\s*)*S&amp;P 500 PE Ratio.*?:\s*</b>\s*([0-9]+(?:\.[0-9]+)?)",
        block,
        re.I | re.S,
    )
    if m:
        pe = float(m.group(1))
    if pe is None:
        m = re.search(
            r"Current S&P 500 PE Ratio is ([0-9]+(?:\.[0-9]+)?)",
            html,
            re.I,
        )
        if m:
            pe = float(m.group(1))
    if pe is None:
        raise RuntimeError("Multpl 首页未能解析当前 PE，疑似页面结构变更")

    valuation_date: date | None = None
    ts = re.search(r'id="timestamp"[^>]*>\s*([^<]+)', html, re.I)
    if ts:
        stamp = re.sub(r"\s+", " ", ts.group(1)).strip()
        # e.g. "4:00 PM EDT, Thu Jul 16"
        m = re.search(
            r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\b",
            stamp,
        )
        if m:
            month_name, day_num = m.group(1), int(m.group(2))
            for year in (today.year, today.year - 1):
                try:
                    candidate = datetime.strptime(
                        f"{month_name} {day_num} {year}", "%b %d %Y"
                    ).date()
                except ValueError:
                    continue
                if candidate <= today + timedelta(days=1):
                    valuation_date = candidate
                    break
    if valuation_date is None:
        valuation_date = today

    return {
        "pe_ttm": round(pe, 2),
        "date": valuation_date.isoformat(),
        "timestamp_raw": ts.group(1).strip() if ts else None,
        "source": MULTPL_HOME_URL,
    }


def _cache_is_fresh(cache: dict, *, today: date | None = None) -> bool:
    today = today or _today()
    fetched_at = cache.get("fetched_at")
    points = cache.get("points") or []
    if not fetched_at or len(points) < MIN_HISTORY_POINTS:
        return False
    try:
        fetched_day = datetime.fromisoformat(str(fetched_at)).date()
    except ValueError:
        return False
    return (today - fetched_day).days <= MONTHLY_CACHE_MAX_AGE_DAYS


def load_or_fetch_monthly_series(*, force_refresh: bool = False) -> dict:
    """Return monthly series. Uses cache when fresh; never trades on failed scrape."""
    cache = _load_json(SPX_CACHE_PATH, {})
    if not force_refresh and _cache_is_fresh(cache):
        return {**cache, "from_cache": True}

    html = _http_get(MULTPL_MONTHLY_URL)
    points = parse_multpl_monthly_html(html)
    payload = {
        "source": MULTPL_MONTHLY_URL,
        "fetched_at": datetime.now(CST).isoformat(timespec="seconds"),
        "points": points,
        "latest_date": points[-1]["date"],
        "latest_pe": points[-1]["pe_ttm"],
        "count": len(points),
        "from_cache": False,
    }
    _write_json(SPX_CACHE_PATH, {k: v for k, v in payload.items() if k != "from_cache"})
    return payload


def fetch_current_pe() -> dict:
    html = _http_get(MULTPL_HOME_URL)
    return parse_multpl_current_html(html)


def _percentile(series: list[float], current: float) -> float:
    return sum(1 for item in series if item <= current) / len(series) * 100


def _window_values(points: list[dict], as_of: date) -> list[float]:
    cutoff = (as_of - timedelta(days=365 * WINDOW_YEARS)).isoformat()
    return [
        float(item["pe_ttm"])
        for item in points
        if item["date"] >= cutoff and isinstance(item.get("pe_ttm"), (int, float))
    ]


def validate_spx(
    pe: float | None,
    percentile: float | None,
    valuation_date: date | None,
    *,
    today: date | None = None,
) -> tuple[bool, list[str]]:
    """Four-layer checks for S&P 500. Fail-closed."""
    today = today or _today()
    errors: list[str] = []
    if pe is None or percentile is None:
        errors.append("估值数据缺失（pe 或 percentile 为空）")
    if valuation_date is None:
        errors.append("估值日期缺失")
    else:
        age = (today - valuation_date).days
        if age < 0:
            errors.append(f"估值日期 {valuation_date} 晚于今天，数据异常")
        elif age > MAX_VALUATION_AGE_DAYS:
            errors.append(
                f"估值日期 {valuation_date} 距今 {age} 天，超过 "
                f"{MAX_VALUATION_AGE_DAYS} 天新鲜度阈值"
            )
    if pe is not None and not (SPX_PE_MIN <= pe <= SPX_PE_MAX):
        errors.append(
            f"PE {pe} 超出合理区间 [{SPX_PE_MIN}, {SPX_PE_MAX}]，疑似脏数据"
        )
    if percentile is not None and not (0.0 <= percentile <= 100.0):
        errors.append(f"分位 {percentile} 非法")
    return (not errors), errors


def _nasdaq_unverified_item() -> dict:
    return {
        "pe_ttm": None,
        "pe_percentile": None,
        "verified": False,
        "status": "unverified",
        "tradeable": False,
        "date": None,
        "window": "无稳定免费指数PE历史序列",
        "source": "hardcoded_unverified",
        "reason": "纳斯达克100暂无稳定可爬的指数PE历史序列，估值未核验，禁止自动买入",
        "validation_errors": ["纳斯达克100硬编码为未核验"],
    }


def refresh_us_pe(*, persist: bool = True, force_monthly_refresh: bool = False) -> dict:
    """Refresh US valuation snapshot. Fail-closed: never mark unverified as tradeable."""
    as_of = _today().isoformat()
    indexes: dict[str, dict] = {}
    alerts: list[str] = []

    try:
        series_doc = load_or_fetch_monthly_series(force_refresh=force_monthly_refresh)
        current = fetch_current_pe()
        pe = float(current["pe_ttm"])
        valuation_date = date.fromisoformat(current["date"])
        window_values = _window_values(series_doc["points"], _today())
        if len(window_values) < MIN_HISTORY_POINTS:
            raise RuntimeError(
                f"近10年样本不足（{len(window_values)} < {MIN_HISTORY_POINTS}）"
            )
        percentile = round(_percentile(window_values, pe), 2)
        ok, errors = validate_spx(pe, percentile, valuation_date)
        cache_note = "（月度序列来自缓存）" if series_doc.get("from_cache") else ""
        item = {
            "pe_ttm": pe,
            "pe_percentile": percentile,
            "verified": ok,
            "tradeable": ok,
            "status": "verified" if ok else "validation_failed",
            "date": current["date"],
            "window": f"近{WINDOW_YEARS}年 Multpl 月度PE分位",
            "source": MULTPL_HOME_URL,
            "history_source": series_doc.get("source", MULTPL_MONTHLY_URL),
            "history_points": len(window_values),
            "history_from_cache": bool(series_doc.get("from_cache")),
            "current_timestamp": current.get("timestamp_raw"),
            "fetched_at": datetime.now(CST).isoformat(timespec="seconds"),
            "reason": (
                f"Multpl 指数PE={pe:.2f}，近10年分位 {percentile:.2f}% "
                f"（样本 {len(window_values)}）{cache_note}"
                if ok
                else "；".join(errors)
            ),
            "validation_errors": errors,
        }
        if not ok:
            item["tradeable"] = False
            item["verified"] = False
            alerts.extend(errors)
        indexes["标普500"] = item
    except Exception as exc:
        message = f"标普500 Multpl 抓取/解析失败：{exc}"
        alerts.append(message)
        indexes["标普500"] = {
            "pe_ttm": None,
            "pe_percentile": None,
            "verified": False,
            "tradeable": False,
            "status": "fetch_failed",
            "date": None,
            "window": f"近{WINDOW_YEARS}年 Multpl 月度PE分位",
            "source": MULTPL_HOME_URL,
            "reason": message + "；禁止使用过期缓存做买卖判断",
            "validation_errors": [message],
        }

    indexes["纳斯达克100"] = _nasdaq_unverified_item()
    # NDX block is permanent policy — keep on the index item, not in daily alerts.

    spx_ok = bool(indexes["标普500"].get("verified"))
    snapshot = {
        "as_of": as_of,
        "updated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "window": "标普：Multpl近10年；纳指：未核验",
        "source": (
            "multpl.com S&P 500 PE (current + monthly history) + local percentile; "
            "NDX hardcoded unverified"
        ),
        "indexes": indexes,
        "alerts": alerts,
        "us_decision_blocked": not spx_ok,
        "nasdaq_buy_blocked": True,
        "has_us_alert": not spx_ok,
    }

    if persist:
        _write_json(SNAPSHOT_PATH, snapshot)
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="刷新美股指数估值（Multpl / 未核验）")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force-monthly-refresh",
        action="store_true",
        help="忽略月度缓存新鲜度，强制重抓 Multpl 月度表",
    )
    args = parser.parse_args()
    snapshot = refresh_us_pe(
        persist=not args.dry_run,
        force_monthly_refresh=args.force_monthly_refresh,
    )
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
