"""Shared valuation / action rules loaded from portfolio_policy.json.

Balanced tiering on near-10y PE (Scheme B):
  A-share: <30% double, 30%~40% full, 40%~60% half, ≥60% stop/take-profit
  US:      <50% full, 50%~70% half, ≥70% stop/take-profit
Micro sleeve (bootstrap): per-index 1y PE + 52w drawdown (+ optional 10y cap);
  may open a small position even in high-10y zones while under micro cap.
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
    """Merge global bootstrap defaults with per-index overrides."""
    cfg = bootstrap_rules(policy)
    by_index = cfg.get("by_index") or {}
    specific = by_index.get(name) or {}
    merged = {
        "percentile_1y_at_or_below": float(
            specific.get(
                "percentile_1y_at_or_below",
                cfg.get("percentile_at_or_below", 60),
            )
        ),
        "require_drawdown_from_52w_high_at_or_above": float(
            specific.get(
                "require_drawdown_from_52w_high_at_or_above",
                cfg.get("require_drawdown_from_52w_high_at_or_above", 0.06),
            )
        ),
        "max_10y_percentile_below": specific.get("max_10y_percentile_below"),
        "require_verified": bool(
            specific.get("require_verified", name in US and name != "纳斯达克100")
        ),
        "qdii_premium_at_or_below": specific.get("qdii_premium_at_or_below"),
    }
    if merged["max_10y_percentile_below"] is not None:
        merged["max_10y_percentile_below"] = float(merged["max_10y_percentile_below"])
    if merged["qdii_premium_at_or_below"] is not None:
        merged["qdii_premium_at_or_below"] = float(merged["qdii_premium_at_or_below"])
    return merged


def allocation_fraction(action: str, policy: dict | None = None) -> float:
    """How much of the sleeve monthly budget to invest for this action."""
    r = rules(policy)
    if action == "double":
        return 2.0
    if action == "buy":
        return 1.0
    if action == "half":
        return float(r.get("a_share_half_fraction", 0.5))
    if action == "bootstrap":
        return 1.0
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
    """Return (action, reason) for an index sleeve using the main 10y rules.

    Actions: buy | double | half | wait | take_profit | premium_block | unknown | reference
    """
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

        buy_below = float(r.get("us_normal_percentile_below", 50))
        half_below = float(r.get("us_half_percentile_below", 70))
        half_frac = float(r.get("us_half_fraction", 0.5))
        take_profit_at = float(r.get("us_take_profit_percentile_at_or_above", 70))
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

        if percentile < buy_below:
            return _premium_gate(
                "buy",
                f"美股近10年分位 {percentile:.2f}% < {buy_below:.0f}%，可研究满额定投",
            )

        if percentile < half_below:
            return _premium_gate(
                "half",
                f"美股近10年分位 {percentile:.2f}% 处于 "
                f"{buy_below:.0f}%~{half_below:.0f}%，按 {half_frac * 100:.0f}% 基础定投",
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

    double_below = float(
        r.get(
            "a_share_double_invest_percentile_below",
            r.get("a_share_double_invest_percentile_at_or_below", 30),
        )
    )
    buy_below = float(r.get("a_share_normal_percentile_below", 40))
    half_below = float(r.get("a_share_half_percentile_below", 60))
    half_frac = float(r.get("a_share_half_fraction", 0.5))
    take_profit_at = float(r.get("a_share_take_profit_percentile_at_or_above", 60))

    if percentile < double_below:
        return (
            "double",
            f"A股近10年分位 {percentile:.2f}% < {double_below:.0f}%，可研究加倍定投",
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
            f"{buy_below:.0f}%~{half_below:.0f}%，按 {half_frac * 100:.0f}% 基础定投",
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
    frac = float(cfg.get("max_fraction_of_target", 0.10))
    return max(0.0, float(target_amount) * frac)


def bootstrap_tranche(target_amount: float, policy: dict | None = None) -> float:
    """Single micro buy size (fraction of target sleeve)."""
    cfg = bootstrap_rules(policy)
    frac = float(cfg.get("tranche_fraction_of_target", 0.05))
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
) -> float:
    """This period's micro amount: min(tranche, remaining)."""
    remaining = bootstrap_remaining(held_cost, target_amount, policy)
    tranche = bootstrap_tranche(target_amount, policy)
    return round(min(tranche, remaining), 2)


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
        return "微建仓未启用"
    max_pct = float(cfg.get("max_fraction_of_target", 0.10)) * 100
    tranche_pct = float(cfg.get("tranche_fraction_of_target", 0.05)) * 100
    parts = []
    for name in ("沪深300", "中证500", "标普500"):
        ic = index_bootstrap_rules(name, policy)
        bit = (
            f"{name}：1年≤{ic['percentile_1y_at_or_below']:.0f}%"
            f"+回撤≥{ic['require_drawdown_from_52w_high_at_or_above'] * 100:.0f}%"
        )
        if ic.get("max_10y_percentile_below") is not None:
            bit += f"+十年＜{ic['max_10y_percentile_below']:.0f}%"
        parts.append(bit)
    return (
        "分指数微建仓（"
        + "；".join(parts)
        + f"；单次约目标仓{tranche_pct:.0f}%、累计≤{max_pct:.0f}%；纳指不自动；点位不单独触发）"
    )


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
) -> tuple[str, str] | None:
    """Per-index micro sleeve; returns None if index not eligible for micro rules."""
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

    threshold = float(ic["percentile_1y_at_or_below"])
    need = float(ic["require_drawdown_from_52w_high_at_or_above"])
    max_10y = ic.get("max_10y_percentile_below")

    if max_10y is not None:
        if percentile is None:
            return None
        if percentile >= max_10y:
            return (
                "wait",
                f"微建仓：近10年分位 {percentile:.2f}% ≥ {max_10y:.0f}% 安全上限，不开微仓",
            )

    if percentile_1y > threshold:
        return None

    if drawdown_from_52w_high is None:
        return (
            "wait",
            "缺少相对52周高点回撤，微建仓失败关闭（不以点位单独买入）",
        )
    if drawdown_from_52w_high < need:
        return (
            "wait",
            f"近1年分位达标但回撤仅 {drawdown_from_52w_high * 100:.2f}% < "
            f"{need * 100:.0f}%（相对52周高点），暂不开微建仓",
        )

    deep = float(cfg.get("deep_drawdown_observe_at_or_above", 0.20))
    if (
        percentile is not None
        and drawdown_from_52w_high >= deep
        and deep_drawdown_observe_reason(percentile, drawdown_from_52w_high, policy)
    ):
        return (
            "wait",
            f"回撤已深（≥{deep * 100:.0f}%）且十年估值仍高，只观察、不开微建仓",
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
                f"微建仓条件满足但 QDII溢价 {premium * 100:.2f}% > "
                f"{premium_cap * 100:.0f}%，暂缓买入",
            )
        if premium is not None and premium > premium_resume:
            return (
                "wait",
                f"微建仓：近1年分位 {percentile_1y:.2f}% ≤ {threshold:.0f}% "
                f"且回撤 {drawdown_from_52w_high * 100:.2f}% "
                f"但溢价 {premium * 100:.2f}% 仍高于 {premium_resume * 100:.0f}%，等待回落",
            )

    cap = bootstrap_cap(target_amount, policy)
    tranche = bootstrap_tranche(target_amount, policy)
    planned = bootstrap_planned_amount(held_cost, target_amount, policy)
    ten_note = (
        f"、十年分位 {percentile:.2f}% ＜ {max_10y:.0f}%"
        if max_10y is not None and percentile is not None
        else ""
    )
    return (
        "bootstrap",
        f"微建仓：近1年分位 {percentile_1y:.2f}% ≤ {threshold:.0f}% "
        f"且相对52周高点回撤 {drawdown_from_52w_high * 100:.2f}% ≥ {need * 100:.0f}%"
        f"{ten_note}（不以指数点位单独触发）；"
        f"单次约 {tranche:.0f} 元，累计上限约 {cap:.0f} 元，本期待建约 {planned:.0f} 元；"
        f"之后仍按十年分位主规则",
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
    deep = float(cfg.get("deep_drawdown_observe_at_or_above", 0.20))
    if drawdown_from_52w_high < deep:
        return None
    r = rules(policy)
    a_stop = float(r.get("a_share_take_profit_percentile_at_or_above", 60))
    us_stop = float(r.get("us_take_profit_percentile_at_or_above", 70))
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
    """Main 10y tiering first; optional per-index micro sleeve; never buy on price alone."""
    primary, reason = classify_index(
        name,
        percentile,
        premium=premium,
        policy=policy,
        verified=verified,
        tradeable=tradeable,
    )
    if primary in ("buy", "double", "reference", "unknown"):
        return primary, reason

    held = float(held_cost or 0)
    if primary == "take_profit" and held > 0:
        observe = deep_drawdown_observe_reason(
            percentile, drawdown_from_52w_high, policy
        )
        if observe:
            return primary, f"{reason}；{observe}"
        return primary, reason

    if primary in ("half", "wait", "take_profit", "premium_block"):
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
            action, boot_reason = boot
            if action == "bootstrap":
                return action, boot_reason
            if action == "premium_block":
                return action, boot_reason
            if primary == "half":
                return primary, reason
            if primary == "wait" and action == "wait":
                return action, boot_reason
            # take_profit / premium_block without holdings: keep micro block reason.
            if primary == "take_profit" and held <= 0 and action == "wait":
                observe = deep_drawdown_observe_reason(
                    percentile, drawdown_from_52w_high, policy
                )
                reason_out = f"高估观察，当前无持仓无需止盈；{boot_reason}"
                if observe:
                    reason_out = f"{reason_out}；{observe}"
                return "overvalued_watch", reason_out

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
        "buy": "可研究满额定投",
        "double": "可研究加倍",
        "half": "半额基础定投",
        "bootstrap": "微建仓可建",
        "wait": "暂停新增",
        "take_profit": "建议分批止盈",
        "overvalued_watch": "高估观察（无持仓）",
        "premium_block": "溢价过高暂缓",
        "unknown": "估值未核验/数据不足",
        "reference": "仅参考·不自动买",
    }.get(action, action)
