"""Shared valuation / action rules loaded from portfolio_policy.json.

10y DCA (Scheme C, relaxed for continuous investing):
  A-share: <30%→300%, 30%~40%→200%, 40%~60%→100%, 60%~90%→50%, ≥90% stop
  US:      <40%→300%, 40%~50%→200%, 50%~70%→100%, 70%~90%→50%, ≥90% stop
1y build tiers (per index): 100% / 50% / 25% of monthly sleeve when
  1y PE + drawdown gates pass; final intensity = max(10y, 1y).
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
    if frac >= 0.999:
        return "buy"
    if frac >= 0.499:
        return "half"
    if frac >= 0.249:
        return "bootstrap"
    return "wait"


def allocation_fraction(action: str, policy: dict | None = None) -> float:
    """How much of the sleeve monthly budget to invest for this action."""
    r = rules(policy)
    if action == "triple":
        return float(r.get("a_share_triple_fraction", 3.0))
    if action == "double":
        return float(r.get("a_share_double_fraction", 2.0))
    if action == "buy":
        return 1.0
    if action == "half":
        return float(r.get("a_share_half_fraction", 0.5))
    if action == "bootstrap":
        return 0.25
    return 0.0


def classify_index(
    name: str,
    percentile: float | None,
    *,
    premium: float | None = None,
    policy: dict | None = None,
    verified: bool | None = None,
    tradeable: bool | None = None,
) -> tuple[str, str]:
    """Return (action, reason) for an index sleeve using the main 10y DCA rules."""
    r = rules(policy)

    if name in US:
        if name == "纳斯达克100":
            return (
                "reference",
                "纳斯达克100仅展示 QQQ 参考估值（stockanalysis/yfinance），未核验，禁止自动买入",
            )
        if verified is not True or tradeable is False:
            return (
                "unknown",
                "标普500估值未核验或校验失败，禁止自动买入/止盈判断",
            )
        if percentile is None:
            return "unknown", "缺少 PE 分位"

        triple_below = float(r.get("us_triple_percentile_below", 40))
        double_below = float(r.get("us_double_percentile_below", 50))
        buy_below = float(r.get("us_normal_percentile_below", 70))
        half_below = float(r.get("us_half_percentile_below", 90))
        half_frac = float(r.get("us_half_fraction", 0.5))
        triple_frac = float(r.get("us_triple_fraction", 3.0))
        double_frac = float(r.get("us_double_fraction", 2.0))
        take_profit_at = float(r.get("us_take_profit_percentile_at_or_above", 90))
        premium_pause = float(r.get("qdii_premium_pause_above", 0.02))
        premium_resume = float(r.get("qdii_premium_resume_below", 0.01))

        if percentile >= take_profit_at:
            return (
                "take_profit",
                f"美股近10年分位 {percentile:.2f}% ≥ {take_profit_at:.0f}%，"
                f"暂停新增并可研究分批止盈1/3~1/2",
            )

        def _premium_gate(base_action: str, base_reason: str) -> tuple[str, str]:
            if premium is not None and premium > premium_pause:
                return (
                    "premium_block",
                    f"QDII溢价 {premium * 100:.2f}% > {premium_pause * 100:.0f}%，暂缓买入",
                )
            if premium is not None and premium > premium_resume:
                return (
                    "wait",
                    f"{base_reason}，但溢价 {premium * 100:.2f}% 仍高于 "
                    f"{premium_resume * 100:.0f}%，等待回落",
                )
            return base_action, base_reason

        if percentile < triple_below:
            return _premium_gate(
                "triple",
                f"美股近10年分位 {percentile:.2f}% < {triple_below:.0f}%，"
                f"可研究 {triple_frac * 100:.0f}% 定投",
            )
        if percentile < double_below:
            return _premium_gate(
                "double",
                f"美股近10年分位 {percentile:.2f}% 处于 "
                f"{triple_below:.0f}%~{double_below:.0f}%，"
                f"可研究 {double_frac * 100:.0f}% 定投",
            )
        if percentile < buy_below:
            return _premium_gate(
                "buy",
                f"美股近10年分位 {percentile:.2f}% 处于 "
                f"{double_below:.0f}%~{buy_below:.0f}%，可研究满额定投",
            )
        if percentile < half_below:
            return _premium_gate(
                "half",
                f"美股近10年分位 {percentile:.2f}% 处于 "
                f"{buy_below:.0f}%~{half_below:.0f}%，按 {half_frac * 100:.0f}% 维持定投",
            )
        if premium is not None and premium > premium_pause:
            return (
                "premium_block",
                f"QDII溢价 {premium * 100:.2f}% > {premium_pause * 100:.0f}%，暂缓买入",
            )
        return (
            "wait",
            f"美股近10年分位 {percentile:.2f}% ≥ {half_below:.0f}%，暂停新增",
        )

    if percentile is None:
        return "unknown", "缺少 PE 分位"

    triple_below = float(r.get("a_share_triple_percentile_below", 30))
    double_below = float(
        r.get(
            "a_share_double_invest_percentile_below",
            r.get("a_share_double_invest_percentile_at_or_below", 40),
        )
    )
    buy_below = float(r.get("a_share_normal_percentile_below", 60))
    half_below = float(r.get("a_share_half_percentile_below", 90))
    half_frac = float(r.get("a_share_half_fraction", 0.5))
    triple_frac = float(r.get("a_share_triple_fraction", 3.0))
    double_frac = float(r.get("a_share_double_fraction", 2.0))
    take_profit_at = float(r.get("a_share_take_profit_percentile_at_or_above", 90))

    if percentile < triple_below:
        return (
            "triple",
            f"A股近10年分位 {percentile:.2f}% < {triple_below:.0f}%，"
            f"可研究 {triple_frac * 100:.0f}% 定投",
        )
    if percentile < double_below:
        return (
            "double",
            f"A股近10年分位 {percentile:.2f}% 处于 "
            f"{triple_below:.0f}%~{double_below:.0f}%，"
            f"可研究 {double_frac * 100:.0f}% 定投",
        )
    if percentile < buy_below:
        return (
            "buy",
            f"A股近10年分位 {percentile:.2f}% 处于 "
            f"{double_below:.0f}%~{buy_below:.0f}%，可研究满额定投",
        )
    if percentile < half_below:
        return (
            "half",
            f"A股近10年分位 {percentile:.2f}% 处于 "
            f"{buy_below:.0f}%~{half_below:.0f}%，按 {half_frac * 100:.0f}% 维持定投",
        )
    if percentile >= take_profit_at:
        return (
            "take_profit",
            f"A股近10年分位 {percentile:.2f}% ≥ {take_profit_at:.0f}%，"
            f"暂停新增并可研究分批止盈1/3~1/2",
        )
    return (
        "wait",
        f"A股近10年分位 {percentile:.2f}% ≥ {half_below:.0f}%，暂停新增",
    )


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
        f"{ten_note} → 本月份额 {frac * 100:.0f}%（与十年定投取高；点位不单独触发）",
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
    """10y DCA + optional 1y build; final intensity = max of the two."""
    primary, reason = classify_index(
        name,
        percentile,
        premium=premium,
        policy=policy,
        verified=verified,
        tradeable=tradeable,
    )
    if primary in ("reference", "unknown"):
        return primary, reason

    held = float(held_cost or 0)
    if primary == "take_profit" and held > 0:
        observe = deep_drawdown_observe_reason(
            percentile, drawdown_from_52w_high, policy
        )
        if observe:
            return primary, f"{reason}；{observe}"
        return primary, reason

    dca_frac = allocation_fraction(primary, policy) if primary not in (
        "take_profit",
        "wait",
        "premium_block",
        "overvalued_watch",
    ) else 0.0

    boot = try_bootstrap_action(
        name,
        percentile=percentile,
        percentile_1y=percentile_1y,
        drawdown_from_52w_high=drawdown_from_52w_high,
        premium=premium,
        policy=policy,
        verified=verified,
        tradeable=tradeable,
        held_cost=held,
        target_amount=target_amount,
    )
    if boot is not None:
        boot_action, boot_reason, boot_frac = boot
        if boot_action == "premium_block":
            return boot_action, boot_reason
        if boot_frac > dca_frac + 1e-9 and boot_action not in ("wait",):
            return boot_action, boot_reason
        if dca_frac <= 0 and boot_action == "wait" and primary in (
            "wait",
            "take_profit",
            "premium_block",
        ):
            if primary == "take_profit" and held <= 0:
                observe = deep_drawdown_observe_reason(
                    percentile, drawdown_from_52w_high, policy
                )
                reason_out = f"高估观察，当前无持仓无需止盈；{boot_reason}"
                if observe:
                    reason_out = f"{reason_out}；{observe}"
                return "overvalued_watch", reason_out
            if primary == "wait":
                return boot_action, boot_reason

    if primary == "take_profit" and held <= 0:
        observe = deep_drawdown_observe_reason(
            percentile, drawdown_from_52w_high, policy
        )
        reason_out = "高估观察，当前无持仓无需止盈"
        if observe:
            reason_out = f"{reason_out}；{observe}"
        return "overvalued_watch", reason_out

    if primary == "half":
        observe = deep_drawdown_observe_reason(
            percentile, drawdown_from_52w_high, policy
        )
        if observe:
            return "half", f"{reason}；{observe}"
        return primary, reason

    if primary in ("triple", "double", "buy"):
        return primary, reason

    if primary == "wait":
        observe = deep_drawdown_observe_reason(
            percentile, drawdown_from_52w_high, policy
        )
        if observe:
            return "wait", f"{reason}；{observe}"

    if primary == "premium_block":
        return primary, reason

    return primary, reason


def decision_label(action: str) -> str:
    return {
        "triple": "可研究3倍定投",
        "buy": "可研究满额定投",
        "double": "可研究2倍定投",
        "half": "半额维持定投",
        "bootstrap": "1年档25%建仓",
        "wait": "暂停新增",
        "take_profit": "建议分批止盈",
        "overvalued_watch": "高估观察（无持仓）",
        "premium_block": "溢价过高暂缓",
        "unknown": "估值未核验/数据不足",
        "reference": "仅参考·不自动买",
    }.get(action, action)
