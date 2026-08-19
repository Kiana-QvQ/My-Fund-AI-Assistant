"""Live A-share index spots (broker-App style, e.g. 沪深300 4582.68).

Uses Sina compact quotes — no pandas / akshare dependency.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Callable

CST = timezone(timedelta(hours=8))

A_SHARE_SPOT_CODES = {
    "沪深300": "s_sh000300",
    "中证500": "s_sh000905",
}


def _today() -> date:
    return datetime.now(CST).date()


def parse_sina_spot_line(raw: str) -> dict | None:
    """Parse one hq_str_s_sh000300=\"沪深300,4582.68,-3.03,-0.07,...\" line."""
    text = (raw or "").strip()
    if "=\"" not in text:
        return None
    payload = text.split("=\"", 1)[1].rstrip('";')
    parts = payload.split(",")
    if len(parts) < 4:
        return None
    try:
        last = float(parts[1])
        change = float(parts[2])
        change_pct = float(parts[3])
    except (TypeError, ValueError):
        return None
    if last <= 0:
        return None
    prev = round(last - change, 4)
    return {
        "name": parts[0],
        "price_spot": round(last, 4),
        "price_change": round(change, 4),
        "price_change_pct": round(change_pct, 2),
        "price_prev_close": prev if prev > 0 else None,
        "source": "sina:hq.sinajs.cn",
        "status": "ok",
    }


def fetch_a_share_spots(
    names: tuple[str, ...] = ("沪深300", "中证500"),
) -> dict[str, dict]:
    """Live index levels matching broker App quotes."""
    import urllib.request

    codes: list[str] = []
    name_by_code: dict[str, str] = {}
    for name in names:
        code = A_SHARE_SPOT_CODES.get(name)
        if not code:
            continue
        codes.append(code)
        name_by_code[code] = name
    if not codes:
        return {}

    url = "https://hq.sinajs.cn/list=" + ",".join(codes)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("gbk", errors="replace")

    out: dict[str, dict] = {}
    for line in body.splitlines():
        parsed = parse_sina_spot_line(line)
        if not parsed:
            continue
        matched_name = None
        for code, name in name_by_code.items():
            if code in line:
                matched_name = name
                break
        if matched_name is None:
            continue
        parsed["index"] = matched_name
        parsed["as_of"] = _today().isoformat()
        out[matched_name] = parsed
    return out


def attach_a_share_spots(
    indexes: dict[str, dict],
    *,
    names: tuple[str, ...] = ("沪深300", "中证500"),
    fetcher: Callable[[tuple[str, ...]], dict[str, dict]] | None = None,
) -> dict[str, dict]:
    """Attach live spot fields; fail soft so README still renders."""
    fetch = fetcher or fetch_a_share_spots
    try:
        spots = fetch(names)
    except Exception as exc:  # noqa: BLE001 — network / encode drift
        for name in names:
            item = indexes.setdefault(name, {})
            item["price_spot_status"] = "fetch_failed"
            item["price_spot_reason"] = str(exc)
        return indexes

    for name in names:
        item = indexes.setdefault(name, {})
        spot = spots.get(name)
        if not spot:
            item["price_spot_status"] = "missing"
            continue
        item["price_spot"] = spot.get("price_spot")
        item["price_change"] = spot.get("price_change")
        item["price_change_pct"] = spot.get("price_change_pct")
        if isinstance(spot.get("price_prev_close"), (int, float)):
            item["price_prev_close"] = spot["price_prev_close"]
        item["price_spot_source"] = spot.get("source")
        item["price_spot_status"] = spot.get("status", "ok")
        item["price_spot_as_of"] = spot.get("as_of")
    return indexes
