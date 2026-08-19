"""Holding average cost helpers and soft buy scaling vs current NAV."""

from __future__ import annotations

from typing import Any

from fund_nav import EQUITY_COST_FUNDS


def avg_cost(cost_basis: float | None, shares: float | None) -> float | None:
    cost = float(cost_basis or 0)
    qty = float(shares or 0)
    if cost <= 0 or qty <= 0:
        return None
    return round(cost / qty, 6)


def vs_avg_pct(nav: float | None, average_cost: float | None) -> float | None:
    if nav is None or average_cost is None:
        return None
    nav_f = float(nav)
    avg = float(average_cost)
    if nav_f <= 0 or avg <= 0:
        return None
    return round((nav_f - avg) / avg * 100.0, 2)


def holding_cost_metrics(holding: dict | None, nav: float | None = None) -> dict[str, Any]:
    holding = holding or {}
    cost = float(holding.get("cost_basis") or 0)
    shares = holding.get("shares")
    shares_f = float(shares) if isinstance(shares, (int, float)) else None
    average = avg_cost(cost, shares_f)
    pct = vs_avg_pct(nav, average)
    return {
        "cost_basis": cost,
        "shares": shares_f,
        "avg_cost": average,
        "nav": float(nav) if isinstance(nav, (int, float)) else None,
        "vs_avg_pct": pct,
    }


def soft_scale_from_policy(policy: dict | None) -> dict[str, Any]:
    rules = (policy or {}).get("rules") or {}
    cfg = dict(rules.get("avg_cost_soft_gate") or {})
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "apply_to": list(cfg.get("apply_to") or list(EQUITY_COST_FUNDS)),
        "premium_start_pct": float(cfg.get("premium_start_pct", 8.0)),
        "premium_full_pct": float(cfg.get("premium_full_pct", 15.0)),
        "min_scale": float(cfg.get("min_scale", 0.5)),
        "exclude_personal_dca": bool(cfg.get("exclude_personal_dca", True)),
        "note": cfg.get("note")
        or "现价相对持仓均价偏高时，组合定投/建仓建议金额软降级；个人纳指日定投不受影响。",
    }


def soft_buy_scale(
    vs_avg_percent: float | None,
    *,
    policy: dict | None = None,
) -> tuple[float, str | None]:
    """Return (scale 0~1, reason). scale=1 means no change."""
    cfg = soft_scale_from_policy(policy)
    if not cfg["enabled"]:
        return 1.0, None
    if vs_avg_percent is None:
        return 1.0, None
    premium = float(vs_avg_percent)
    start = float(cfg["premium_start_pct"])
    full = float(cfg["premium_full_pct"])
    min_scale = max(0.0, min(1.0, float(cfg["min_scale"])))
    if premium < start:
        return 1.0, None
    if full <= start:
        scale = min_scale
    else:
        # Linear from 1.0 at start → min_scale at full.
        t = min(1.0, max(0.0, (premium - start) / (full - start)))
        scale = round(1.0 - t * (1.0 - min_scale), 4)
    reason = (
        f"相对持仓均价 +{premium:.1f}% "
        f"（≥{start:.0f}% 开始软降，满仓降级点 {full:.0f}%），"
        f"建议金额 ×{scale:.2f}"
    )
    return scale, reason


def apply_soft_buy_scale(
    amount: float,
    *,
    fund_code: str | None,
    vs_avg_percent: float | None,
    policy: dict | None = None,
    is_personal_dca: bool = False,
) -> tuple[float, str | None, float]:
    """Scale a suggested buy amount. Returns (new_amount, reason|None, scale)."""
    cfg = soft_scale_from_policy(policy)
    code = str(fund_code or "")
    if is_personal_dca and cfg["exclude_personal_dca"]:
        return float(amount or 0), None, 1.0
    if code and code not in set(cfg["apply_to"]):
        return float(amount or 0), None, 1.0
    scale, reason = soft_buy_scale(vs_avg_percent, policy=policy)
    amt = round(float(amount or 0) * scale, 2)
    return amt, reason, scale


def implied_avg_index_level(
    price_close: float | None,
    avg_cost: float | None,
    nav: float | None,
) -> float | None:
    """Estimate buy-average index level from current close × (avg_cost / nav).

    Assumes the linked fund tracks the index in percentage terms (fees/tracking
    error ignored). Same direction as vs_avg_pct.
    """
    if (
        not isinstance(price_close, (int, float))
        or not isinstance(avg_cost, (int, float))
        or not isinstance(nav, (int, float))
    ):
        return None
    close = float(price_close)
    avg = float(avg_cost)
    nav_f = float(nav)
    if close <= 0 or avg <= 0 or nav_f <= 0:
        return None
    return round(close * (avg / nav_f), 4)


def format_index_level(value: float | None) -> str:
    """App-style index level (e.g. 4582.68), not truncated integers."""
    if not isinstance(value, (int, float)):
        return "-"
    level = float(value)
    if level >= 100:
        return f"{level:.2f}"
    return f"{level:.2f}"


def format_avg_cost_bit(metrics_or_line: dict | None) -> str:
    """Compact email/readme snippet: 均价/净值/相对均价."""
    row = metrics_or_line or {}
    avg = row.get("avg_cost")
    nav = row.get("nav")
    vs = row.get("vs_avg_pct")
    scale = row.get("avg_cost_scale")
    bits: list[str] = []
    if isinstance(avg, (int, float)):
        bits.append(f"均价 {float(avg):.4f}")
    if isinstance(nav, (int, float)):
        bits.append(f"净值 {float(nav):.4f}")
    if isinstance(vs, (int, float)):
        bits.append(f"相对均价 {float(vs):+.2f}%")
    if isinstance(scale, (int, float)) and float(scale) < 0.999:
        bits.append(f"软降×{float(scale):.2f}")
    return "｜".join(bits)


def holdings_by_code(holdings_doc: dict | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in (holdings_doc or {}).get("holdings") or []:
        code = str(row.get("fund_code") or "")
        if code:
            out[code] = row
    return out
