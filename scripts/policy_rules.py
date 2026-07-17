"""Shared valuation / action rules loaded from portfolio_policy.json.

10y DCA (A-share and US same ladder):
  <40%→300%, 40%~50%→200%, 50%~60%→150%, 60%~70%→100%,
  70%~80%→70%, 80%~90%→50%, ≥90% stop / take-profit.
1y build tiers are independent (never merged into DCA).
Index absolute price/level never triggers buys alone.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "portfolio_policy.json"

A_SHARE = ("沪深300", "中证500")
US = ("标普500", "纳斯达克100")


def load_policy(path: Path = POLICY_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rules(policy: dict | None = None) -> dict:
    policy = policy or load_policy()
    return policy.get("rules", {})


def bootstrap_rules(policy: dict | None = None) -> dict:
    policy = policy or load_policy()
    return policy.get("bootstrap", {})


def index_bootstrap_rules(name: str, policy: dict | None = None) -> dict:
    """Per-index build config (tiers + optional 10y safety / premium)."""
    cfg = bootstrap_rules(policy)
    by_index = cfg.get("by_index") or {}
    specific = dict(by_index.get(name) or {})
    if "max_10y_percentile_below" in specific and specific["max_10y_percentile_below"] is not None:
        specific["max_10y_percentile_below"] = float(specific["max_10y_percentile_below"])
    if "qdii_premium_at_or_below" in specific and specific["qdii_premium_at_or_below"] is not None:
        specific["qdii_premium_at_or_below"] = float(specific["qdii_premium_at_or_below"])
    specific.setdefault(
        "require_verified", name in US and name != "纳斯达克100"
    )
    specific["require_verified"] = bool(specific["require_verified"])
    return specific


def action_from_fraction(frac: float) -> str:
    if frac >= 2.999:
        return "triple"
    if frac >= 1.999:
        return "double"
    if frac >= 1.499:
        return "boost_150"
    if frac >= 0.999:
        return "buy"
    if frac >= 0.699:
        return "seventy"
    if frac >= 0.499:
        return "half"
    if frac >= 0.249:
        return "bootstrap"
    return "wait"


def allocation_fraction(action: str, policy: dict | None = None) -> float:
    """How much of the sleeve monthly budget to invest for this action."""
    mapping = {
        "triple": 3.0,
        "double": 2.0,
        "boost_150": 1.5,
        "buy": 1.0,
        "seventy": 0.7,
        "three_quarter": 0.75,
        "half": 0.5,
        "bootstrap": 0.25,
    }
    return float(mapping.get(action, 0.0))


def classify_index(
    name: str,
    percentile: float | None,
    *,
    premium: float | None = None,
    drawdown_from_52w_high: float | None = None,
    policy: dict | None = None,
    verified: bool | None = None,
    tradeable: bool | None = None,
) -> tuple[str, str]:
    """Public classifier — always delegates to resolve_dca_line (single source of truth)."""
    from investment_plan import resolve_dca_line  # noqa: WPS433

    line = resolve_dca_line(
        name,
        percentile,
        premium=premium,
        drawdown_from_52w_high=drawdown_from_52w_high,
        policy=policy,
        verified=verified,
        tradeable=tradeable,
    )
    return line["action"], line["reason"]


def bootstrap_cap(target_amount: float, policy: dict | None = None) -> float:
    cfg = bootstrap_rules(policy)
    frac = float(cfg.get("max_fraction_of_target", 0.30))
    return max(0.0, float(target_amount) * frac)


def bootstrap_remaining(
    held_cost: float,
    target_amount: float,
    policy: dict | None = None,
) -> float:
    return max(0.0, bootstrap_cap(target_amount, policy) - float(held_cost or 0))


def bootstrap_planned_amount(
    held_cost: float,
    target_amount: float,
    policy: dict | None = None,
    *,
    month_slice: float | None = None,
    fraction: float = 0.25,
) -> float:
    """Build amount for this period: min(month_slice * fraction, remaining)."""
    remaining = bootstrap_remaining(held_cost, target_amount, policy)
    if month_slice is None:
        # Fallback: fraction of remaining / target sleeve when monthly unknown.
        raw = bootstrap_cap(target_amount, policy) * float(fraction)
    else:
        raw = float(month_slice) * float(fraction)
    return round(min(raw, remaining), 2)


def _bootstrap_allowed_for_index(name: str, policy: dict | None = None) -> bool:
    cfg = bootstrap_rules(policy)
    if not cfg.get("enabled", False):
        return False
    exclude = cfg.get("exclude") or ["纳斯达克100"]
    if name in exclude:
        return False
    allow = cfg.get("indexes")
    if allow is not None and name not in allow:
        return False
    return True


def bootstrap_summary_line(policy: dict | None = None) -> str:
    cfg = bootstrap_rules(policy)
    if not cfg.get("enabled"):
        return "1年建仓未启用"
    max_pct = float(cfg.get("max_fraction_of_target", 0.30)) * 100
    parts = []
    for name in ("沪深300", "中证500", "标普500"):
        ic = index_bootstrap_rules(name, policy)
        tiers = ic.get("tiers") or []
        bits = []
        for tier in tiers:
            bits.append(
                f"1年≤{float(tier['percentile_1y_at_or_below']):.0f}%→"
                f"{float(tier['fraction']) * 100:.0f}%"
            )
        parts.append(f"{name}[{'/'.join(bits)}]")
    return (
        "1年建仓三档（与十年定投取较高者）："
        + "；".join(parts)
        + f"；累计≤目标仓{max_pct:.0f}%；纳指不自动；点位不单独触发"
    )


def _match_build_tier(
    percentile_1y: float,
    drawdown: float | None,
    tiers: list[dict],
) -> tuple[float, float, float] | None:
    """Return (fraction, 1y_threshold, drawdown_need) for the best matching tier.

    Prefer higher fraction (100% > 50% > 25%) among tiers whose 1y + drawdown gates pass.
    """
    if drawdown is None:
        return None
    ordered = sorted(tiers, key=lambda t: -float(t["fraction"]))
    for tier in ordered:
        thr = float(tier["percentile_1y_at_or_below"])
        frac = float(tier["fraction"])
        need = float(tier.get("drawdown_at_or_above", 0.0))
        if percentile_1y <= thr and drawdown >= need:
            return frac, thr, need
    return None


def try_bootstrap_action(
    name: str,
    *,
    percentile: float | None = None,
    percentile_1y: float | None,
    drawdown_from_52w_high: float | None = None,
    premium: float | None = None,
    policy: dict | None = None,
    verified: bool | None = None,
    tradeable: bool | None = None,
    held_cost: float = 0.0,
    target_amount: float = 0.0,
) -> tuple[str, str, float] | None:
    """1y build tiers → (action, reason, fraction). None if index not eligible."""
    if not _bootstrap_allowed_for_index(name, policy):
        return None

    ic = index_bootstrap_rules(name, policy)
    if ic.get("require_verified") and (verified is not True or tradeable is False):
        return None
    if percentile_1y is None:
        return None

    cfg = bootstrap_rules(policy)
    min_amount = float(cfg.get("min_amount", 10))
    remaining = bootstrap_remaining(held_cost, target_amount, policy)
    if remaining < min_amount:
        return None

    max_10y = ic.get("max_10y_percentile_below")
    if max_10y is not None:
        if percentile is None:
            return None
        if percentile >= max_10y:
            return (
                "wait",
                f"1年建仓：近10年分位 {percentile:.2f}% ≥ {max_10y:.0f}% 安全上限，不开建仓",
                0.0,
            )

    tiers = ic.get("tiers") or []
    if not tiers:
        return None

    # Soft miss reasons when 1y in range but drawdown missing/low
    in_any_1y = any(percentile_1y <= float(t["percentile_1y_at_or_below"]) for t in tiers)
    matched = _match_build_tier(percentile_1y, drawdown_from_52w_high, tiers)
    if matched is None:
        if not in_any_1y:
            return None
        if drawdown_from_52w_high is None:
            return (
                "wait",
                "缺少相对52周高点回撤，1年建仓失败关闭（不以点位单独买入）",
                0.0,
            )
        # Find the loosest tier that 1y would qualify for, to explain drawdown miss.
        loose = max(tiers, key=lambda t: float(t["percentile_1y_at_or_below"]))
        need = float(loose.get("drawdown_at_or_above", 0.0))
        return (
            "wait",
            f"近1年分位达标但回撤仅 {drawdown_from_52w_high * 100:.2f}% < "
            f"{need * 100:.0f}%（相对52周高点），暂不按1年档建仓",
            0.0,
        )

    frac, thr, need = matched

    deep = float(cfg.get("deep_drawdown_observe_at_or_above", 0.25))
    if (
        percentile is not None
        and drawdown_from_52w_high is not None
        and drawdown_from_52w_high >= deep
        and deep_drawdown_observe_reason(percentile, drawdown_from_52w_high, policy)
    ):
        return (
            "wait",
            f"回撤已深（≥{deep * 100:.0f}%）且十年估值仍高，只观察、不按1年档加仓",
            0.0,
        )

    r = rules(policy)
    premium_cap = ic.get("qdii_premium_at_or_below")
    if premium_cap is None and name in US:
        premium_cap = float(r.get("qdii_premium_pause_above", 0.02))
    premium_resume = float(r.get("qdii_premium_resume_below", 0.01))
    if name in US and premium_cap is not None:
        if premium is None:
            return (
                "premium_block",
                "1年建仓条件满足但 QDII 溢价数据缺失，暂停建仓（fail-closed）",
                0.0,
            )
        if premium is not None and premium > premium_cap:
            return (
                "premium_block",
                f"1年建仓条件满足但 QDII溢价 {premium * 100:.2f}% > "
                f"{premium_cap * 100:.0f}%，暂缓买入",
                0.0,
            )
        if premium is not None and premium > premium_resume:
            return (
                "wait",
                f"1年建仓：近1年分位 {percentile_1y:.2f}% ≤ {thr:.0f}% "
                f"且回撤 {drawdown_from_52w_high * 100:.2f}% "
                f"但溢价 {premium * 100:.2f}% 仍高于 {premium_resume * 100:.0f}%，等待回落",
                0.0,
            )

    assert drawdown_from_52w_high is not None
    action = action_from_fraction(frac)
    ten_note = (
        f"、十年分位 {percentile:.2f}% ＜ {max_10y:.0f}%"
        if max_10y is not None and percentile is not None
        else ""
    )
    return (
        action,
        f"1年建仓档：近1年分位 {percentile_1y:.2f}% ≤ {thr:.0f}% "
        f"且回撤 {drawdown_from_52w_high * 100:.2f}% ≥ {need * 100:.0f}%"
        f"{ten_note} → 建仓份额 {frac * 100:.0f}%（独立于周度定投；点位不单独触发）",
        frac,
    )


def deep_drawdown_observe_reason(
    percentile: float | None,
    drawdown_from_52w_high: float | None,
    policy: dict | None = None,
) -> str | None:
    """If PE still high but drawdown already deep → observe, do not dip-buy."""
    if percentile is None or drawdown_from_52w_high is None:
        return None
    cfg = bootstrap_rules(policy)
    deep = float(cfg.get("deep_drawdown_observe_at_or_above", 0.25))
    if drawdown_from_52w_high < deep:
        return None
    r = rules(policy)
    a_stop = float(r.get("a_share_take_profit_percentile_at_or_above", 90))
    us_stop = float(r.get("us_take_profit_percentile_at_or_above", 90))
    high_bar = min(a_stop, us_stop)
    if percentile < high_bar:
        return None
    return (
        f"相对52周高点回撤已达 {drawdown_from_52w_high * 100:.2f}% ≥ {deep * 100:.0f}%，"
        f"但近10年PE分位 {percentile:.2f}% 仍偏高；只观察、不因跌幅盲目抄底"
    )


def resolve_action(
    name: str,
    percentile: float | None,
    *,
    percentile_1y: float | None = None,
    drawdown_from_52w_high: float | None = None,
    premium: float | None = None,
    policy: dict | None = None,
    verified: bool | None = None,
    tradeable: bool | None = None,
    held_cost: float = 0.0,
    target_amount: float = 0.0,
) -> tuple[str, str]:
    """Dashboard helper: DCA-only status (build is separate; never merged)."""
    # Local import avoids cycles with investment_plan ↔ policy_rules.
    from investment_plan import (  # noqa: WPS433
        dca_config,
        portfolio_monthly_base,
        resolve_dca_line,
        sleeve_weight,
    )

    held = float(held_cost or 0)
    line = resolve_dca_line(
        name,
        percentile,
        premium=premium,
        drawdown_from_52w_high=drawdown_from_52w_high,
        policy=policy,
        verified=verified,
        tradeable=tradeable,
    )
    action = line["action"]
    reason = line["reason"]

    if action == "reference":
        return action, reason
    if action == "unknown":
        return action, reason
    if action == "premium_block":
        return action, reason

    pause_at = float(
        (policy or load_policy()).get("dca", {}).get("pause_10y_at_or_above", 90)
    )
    if (
        percentile is not None
        and percentile >= pause_at
        and held > 0
    ):
        observe = deep_drawdown_observe_reason(
            percentile, drawdown_from_52w_high, policy
        )
        tp_reason = (
            f"近10年分位 {percentile:.2f}% ≥ {pause_at:.0f}%，"
            f"定投暂停并可研究分批止盈1/3~1/2"
        )
        if observe:
            tp_reason = f"{tp_reason}；{observe}"
        return "take_profit", tp_reason

    if action == "overvalued_watch" and held <= 0:
        observe = deep_drawdown_observe_reason(
            percentile, drawdown_from_52w_high, policy
        )
        reason_out = "高估观察，当前无持仓无需止盈；定投已暂停"
        if observe:
            reason_out = f"{reason_out}；{observe}"
        return "overvalued_watch", reason_out

    if line["paused"]:
        return "wait", reason

    # Ideal sleeve share of portfolio base (before priority/cap trim)
    pol = policy or load_policy()
    weight = 0.0
    for sleeve in dca_config(pol).get("sleeves") or []:
        if sleeve.get("index") == name or sleeve.get("name") == name:
            weight = sleeve_weight(sleeve, pol)
            break
    ideal = round(portfolio_monthly_base(pol) * weight * float(line["multiplier"]), 2)
    return action, f"{reason}；组合份额约 {ideal:.0f} 元/月（总额基础300封顶1000）"


def decision_label(action: str) -> str:
    return {
        "triple": "定投300%",
        "double": "定投200%",
        "boost_150": "定投150%",
        "buy": "定投100%",
        "seventy": "定投70%",
        "three_quarter": "定投75%",
        "half": "定投50%",
        "bootstrap": "定投25%/建仓档",
        "wait": "定投暂停",
        "take_profit": "建议分批止盈",
        "overvalued_watch": "高估观察（无持仓）",
        "premium_block": "溢价过高暂缓",
        "unknown": "估值未核验/数据不足",
        "reference": "仅参考·不自动买",
    }.get(action, action)
