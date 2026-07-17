"""DCA (weekly) and build (event) plan helpers — independent of each other.

Portfolio budget model:
- monthly_base / monthly_cap apply to the **whole plan** (default 300 / 1000)
- Split across 5 sleeves by target_allocation weights
- Paused / excluded equity weight flows to 短债
- Weekly size = remaining month budget ÷ remaining Thursdays (enforces month cap)
"""

from __future__ import annotations

import calendar
import sys
from datetime import date
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from policy_rules import (
    A_SHARE,
    US,
    action_from_fraction,
    bootstrap_rules,
    decision_label,
    load_policy,
)


def dca_config(policy: dict | None = None) -> dict:
    policy = policy or load_policy()
    return policy.get("dca") or {}


def portfolio_monthly_base(policy: dict | None = None) -> float:
    cfg = dca_config(policy)
    if "monthly_base" in cfg:
        return float(cfg["monthly_base"])
    # Backward-compatible key from older drafts
    return float(cfg.get("per_index_monthly_base", 300))


def portfolio_monthly_cap(policy: dict | None = None) -> float:
    cfg = dca_config(policy)
    if "monthly_cap" in cfg:
        return float(cfg["monthly_cap"])
    return float(cfg.get("per_index_monthly_cap", 1000))


def full_cap_multiplier(policy: dict | None = None) -> float:
    return float(dca_config(policy).get("full_cap_multiplier", 3.0))


def _tiers_for(name: str, policy: dict | None = None) -> list[dict]:
    cfg = dca_config(policy)
    if name in A_SHARE:
        return list(cfg.get("a_share_tiers") or [])
    if name == "标普500":
        return list(cfg.get("us_tiers") or [])
    return []


def dca_allowed_index(name: str, policy: dict | None = None) -> bool:
    """True if index may receive equity DCA (纳指 excluded)."""
    cfg = dca_config(policy)
    for sleeve in cfg.get("sleeves") or []:
        if sleeve.get("index") == name or sleeve.get("name") == name:
            return sleeve.get("role") == "equity"
    exclude = cfg.get("exclude") or ["纳斯达克100"]
    return name not in exclude


def sleeve_weight(sleeve: dict, policy: dict | None = None) -> float:
    policy = policy or load_policy()
    alloc = policy.get("target_allocation") or {}
    key = str(sleeve.get("weight_key") or "")
    return float(alloc.get(key, 0.0) or 0.0)


def multiplier_from_percentile(
    name: str,
    percentile: float | None,
    *,
    policy: dict | None = None,
) -> tuple[float, str]:
    """Map 10y PE percentile → DCA multiplier.

    Pause rule: **≥90%** 停买（恰好 90% 也不买）。
    """
    if percentile is None:
        return 0.0, "缺少近10年PE分位，定投暂停"
    cfg = dca_config(policy)
    pause_at = float(
        cfg.get("pause_10y_at_or_above", cfg.get("pause_10y_above", 90))
    )
    if percentile >= pause_at:
        return 0.0, f"近10年分位 {percentile:.2f}% ≥ {pause_at:.0f}%，定投暂停"

    for tier in _tiers_for(name, policy):
        below = float(tier["percentile_below"])
        mult = float(tier["multiplier"])
        if percentile < below:
            return mult, f"近10年分位 {percentile:.2f}% → 倍率 {mult * 100:.0f}%"
    return 0.0, f"近10年分位 {percentile:.2f}% 未匹配定投档，暂停"


def thursdays_in_month(year: int, month: int) -> list[date]:
    days = calendar.monthcalendar(year, month)
    return [date(year, month, week[calendar.THURSDAY]) for week in days if week[calendar.THURSDAY]]


def remaining_thursdays(today: date) -> list[date]:
    return [d for d in thursdays_in_month(today.year, today.month) if d >= today]


def resolve_dca_line(
    name: str,
    percentile: float | None,
    *,
    premium: float | None = None,
    drawdown_from_52w_high: float | None = None,
    policy: dict | None = None,
    verified: bool | None = None,
    tradeable: bool | None = None,
) -> dict:
    """Equity-index DCA multiplier only (amounts filled by allocate_dca_plan)."""
    policy = policy or load_policy()
    rules = policy.get("rules") or {}
    cfg = dca_config(policy)
    premium_pause = float(rules.get("qdii_premium_pause_above", 0.02))
    premium_resume = float(rules.get("qdii_premium_resume_below", 0.01))
    near_high_dd = float(cfg.get("stop_boost_drawdown_below", 0.01))
    require_dd = bool(cfg.get("require_drawdown_for_boost", True))

    if not dca_allowed_index(name, policy):
        return {
            "name": name,
            "action": "reference",
            "multiplier": 0.0,
            "monthly": 0.0,
            "weekly": 0.0,
            "reason": "该标的不参与自动定投（份额归短债）",
            "paused": True,
        }

    if name == "标普500" and (verified is not True or tradeable is False):
        return {
            "name": name,
            "action": "unknown",
            "multiplier": 0.0,
            "monthly": 0.0,
            "weekly": 0.0,
            "reason": "标普估值未核验，定投暂停（fail-closed）",
            "paused": True,
        }

    if name in US and premium is not None and premium > premium_pause:
        return {
            "name": name,
            "action": "premium_block",
            "multiplier": 0.0,
            "monthly": 0.0,
            "weekly": 0.0,
            "reason": (
                f"QDII溢价 {premium * 100:.2f}% > {premium_pause * 100:.0f}%，"
                f"美股定投暂停"
            ),
            "paused": True,
        }
    if name in US and premium is None:
        return {
            "name": name,
            "action": "premium_block",
            "multiplier": 0.0,
            "monthly": 0.0,
            "weekly": 0.0,
            "reason": "QDII溢价数据缺失，暂停对应美股定投（fail-closed）",
            "paused": True,
        }
    if name in US and premium > premium_resume:
        return {
            "name": name,
            "action": "wait",
            "multiplier": 0.0,
            "monthly": 0.0,
            "weekly": 0.0,
            "reason": (
                f"QDII溢价 {premium * 100:.2f}% > "
                f"{premium_resume * 100:.0f}%，等待回落后恢复定投"
            ),
            "paused": True,
        }

    mult, reason = multiplier_from_percentile(name, percentile, policy=policy)
    if mult > 1.0:
        if require_dd and drawdown_from_52w_high is None:
            mult = 1.0
            reason = f"{reason}；缺回撤数据，禁止加码、维持 100%"
        elif (
            drawdown_from_52w_high is not None
            and drawdown_from_52w_high < near_high_dd
        ):
            mult = 1.0
            reason = (
                f"{reason}；近52周高点（回撤 {drawdown_from_52w_high * 100:.2f}%），"
                f"停止加码、维持基础倍率 100%"
            )

    action = action_from_fraction(mult) if mult > 0 else "wait"
    if mult <= 0 and percentile is not None:
        pause_at = float(
            cfg.get("pause_10y_at_or_above", cfg.get("pause_10y_above", 90))
        )
        if percentile >= pause_at:
            action = "overvalued_watch"
    return {
        "name": name,
        "action": action,
        "multiplier": mult,
        "monthly": 0.0,
        "weekly": 0.0,
        "reason": reason,
        "paused": mult <= 0,
        "label": decision_label(action) if mult > 0 else "定投暂停",
    }


def _index_multiplier_map(equity_lines: list[dict]) -> dict[str, dict]:
    return {line["name"]: line for line in equity_lines}


def allocate_dca_plan(
    equity_lines: list[dict],
    *,
    policy: dict | None = None,
    today: date | None = None,
    month_spent: float = 0.0,
) -> list[dict]:
    """Allocate portfolio monthly_base..cap across 5 sleeves by weight + priority.

    Weekly = remaining month budget / remaining Thursdays in this calendar month.
    """
    policy = policy or load_policy()
    cfg = dca_config(policy)
    base = portfolio_monthly_base(policy)
    cap = portfolio_monthly_cap(policy)
    today = today or date.today()
    sleeves = list(cfg.get("sleeves") or [])
    priority = list(cfg.get("priority") or [s["name"] for s in sleeves])
    by_index = _index_multiplier_map(equity_lines)

    # Ideal wants before portfolio cap (paused/excluded → 0, weight to residual later)
    wants: dict[str, float] = {}
    meta: dict[str, dict] = {}
    residual_weight = 0.0
    for sleeve in sleeves:
        name = str(sleeve["name"])
        weight = sleeve_weight(sleeve, policy)
        role = sleeve.get("role")
        meta[name] = sleeve
        if role == "residual":
            wants[name] = round(base * weight, 4)
            continue
        if role == "excluded":
            wants[name] = 0.0
            residual_weight += weight
            continue
        # equity
        index_name = str(sleeve.get("index") or name)
        src = by_index.get(index_name) or {
            "multiplier": 0.0,
            "paused": True,
            "reason": "缺少权益定投行",
            "action": "wait",
        }
        mult = float(src.get("multiplier") or 0.0)
        if mult <= 0:
            wants[name] = 0.0
            residual_weight += weight
        else:
            wants[name] = round(base * weight * mult, 4)

    # Redirect excluded/paused equity weight at 100% into 短债 base sleeve
    residual_name = next(
        (s["name"] for s in sleeves if s.get("role") == "residual"), "短债"
    )
    wants[residual_name] = round(wants.get(residual_name, 0.0) + base * residual_weight, 4)

    # Portfolio budget:
    # - ≤100%：按权重×倍率汇总，不人为抬回 300
    # - 100%~300%：上浮到 min(封顶, 基础×最高倍率)
    # - 达到 full_cap_multiplier（默认300%）：解锁组合月封顶 1000
    equity_multipliers = [
        float(line.get("multiplier") or 0.0)
        for line in equity_lines
        if float(line.get("multiplier") or 0.0) > 0
    ]
    max_multiplier = max(equity_multipliers, default=0.0)
    desired_total = round(sum(wants.values()), 2)
    full_at = full_cap_multiplier(policy)
    if max_multiplier <= 0:
        budget_target = desired_total
    elif max_multiplier >= full_at:
        budget_target = cap
    elif max_multiplier > 1.0:
        budget_target = min(cap, round(base * max_multiplier, 2))
        budget_target = max(budget_target, desired_total)
    else:
        budget_target = desired_total
    budget_target = min(cap, round(budget_target, 2))

    if desired_total > 0 and budget_target > desired_total + 1e-9:
        scale = budget_target / desired_total
        wants = {name: round(amount * scale, 4) for name, amount in wants.items()}

    # Priority fill under portfolio monthly_cap
    remaining_cap = cap
    monthly: dict[str, float] = {s["name"]: 0.0 for s in sleeves}
    for name in priority:
        if name not in wants:
            continue
        got = min(wants[name], remaining_cap)
        got = round(got, 2)
        monthly[name] = got
        remaining_cap = round(remaining_cap - got, 2)

    # Any leftover after priority (shouldn't happen if wants<=cap) → 短债
    if remaining_cap > 0 and sum(wants.values()) > sum(monthly.values()):
        # Cap bound the total; leftover capacity unused on purpose
        pass

    month_target = round(sum(monthly.values()), 2)
    left_thursdays = remaining_thursdays(today)
    n_left = max(1, len(left_thursdays))
    # If today is not Thursday, still show "next slot" share using remaining Thursdays
    spent = max(0.0, float(month_spent or 0.0))
    if today.strftime("%Y-%m") and spent > 0:
        # spent only counts in same month — caller must reset
        pass
    remain_budget = max(0.0, round(month_target - spent, 2))
    week_total = round(remain_budget / n_left, 2) if month_target > 0 else 0.0

    lines: list[dict] = []
    for sleeve in sleeves:
        name = str(sleeve["name"])
        role = sleeve.get("role")
        index_name = str(sleeve.get("index") or name)
        m_amt = float(monthly.get(name, 0.0))
        if month_target > 0 and week_total > 0:
            weekly = round(m_amt / month_target * week_total, 2)
        else:
            weekly = 0.0

        if role == "residual":
            reason = "目标仓位短债底仓；权益暂停/纳指份额并入"
            action = "buy" if weekly > 0 else "wait"
            paused = weekly <= 0
            mult = 1.0
        elif role == "excluded":
            src = by_index.get(index_name, {})
            reason = src.get("reason") or "纳指不自动定投，份额归短债"
            action = "reference"
            paused = True
            mult = 0.0
            weekly = 0.0
            m_amt = 0.0
        else:
            src = by_index.get(index_name, {})
            mult = float(src.get("multiplier") or 0.0)
            reason = str(src.get("reason") or "")
            action = src.get("action") or ("wait" if mult <= 0 else action_from_fraction(mult))
            paused = bool(src.get("paused")) or mult <= 0
            if paused:
                weekly = 0.0
                m_amt = 0.0

        lines.append(
            {
                "name": name,
                "fund_code": sleeve.get("fund_code"),
                "fund_name": sleeve.get("fund_name"),
                "role": role,
                "weight": sleeve_weight(sleeve, policy),
                "action": action,
                "multiplier": mult,
                "monthly": m_amt,
                "weekly": weekly,
                "reason": reason,
                "paused": paused,
                "label": decision_label(action) if not paused and weekly > 0 else (
                    "定投暂停" if paused else decision_label(action)
                ),
                "month_target_total": month_target,
                "month_spent": spent,
                "month_remaining": remain_budget,
                "thursdays_left": n_left,
            }
        )

    # Reconcile per-sleeve rounding so weekly totals never exceed the
    # remaining monthly budget when split across several funds.
    rounded_week_total = round(sum(float(line["weekly"]) for line in lines), 2)
    rounding_delta = round(week_total - rounded_week_total, 2)
    if lines and rounding_delta:
        candidates = [
            index for index, line in enumerate(lines) if float(line["weekly"]) > 0
        ]
        if candidates:
            index = candidates[-1]
            adjusted = round(float(lines[index]["weekly"]) + rounding_delta, 2)
            if adjusted >= 0:
                lines[index]["weekly"] = adjusted

    # Fix equity paused rows that still got monthly from wants=0 — already 0
    return lines


def dca_amounts(
    multiplier: float,
    *,
    policy: dict | None = None,
    weight: float = 1.0,
) -> tuple[float, float]:
    """Ideal sleeve monthly before portfolio priority/cap; weekly ≈ / remaining not known → /4."""
    base = portfolio_monthly_base(policy)
    cap = portfolio_monthly_cap(policy)
    monthly = min(base * float(weight) * float(multiplier), cap) if multiplier > 0 else 0.0
    weekly = round(monthly / 4.0, 2)
    return round(monthly, 2), weekly


def resolve_build_line(
    name: str,
    percentile: float | None,
    *,
    percentile_1y: float | None,
    drawdown_from_52w_high: float | None = None,
    premium: float | None = None,
    policy: dict | None = None,
    verified: bool | None = None,
    tradeable: bool | None = None,
    held_cost: float = 0.0,
    target_amount: float = 0.0,
    month_slice: float | None = None,
    drawdown_status: str | None = None,
    pe_status: str | None = None,
) -> dict:
    """Independent build decision (event email / state machine)."""
    from build_state_machine import observe_build_state  # noqa: WPS433

    observed = observe_build_state(
        name,
        percentile=percentile,
        percentile_1y=percentile_1y,
        drawdown_from_52w_high=drawdown_from_52w_high,
        premium=premium,
        drawdown_status=drawdown_status,
        pe_status=pe_status,
        policy=policy,
        verified=verified,
        tradeable=tradeable,
        held_cost=held_cost,
        target_amount=target_amount,
        month_slice=month_slice,
    )
    return {
        "name": name,
        "active": bool(observed.get("active")),
        "action": observed.get("action") or "none",
        "fraction": float(observed.get("fraction") or 0),
        "amount": float(observed.get("amount") or 0),
        "tier_label": observed.get("tier_label") or observed.get("state") or "不可买",
        "state": observed.get("state") or "不可买",
        "reason": observed.get("reason") or "",
        "needs_human_confirm": bool(observed.get("needs_human_confirm", True)),
    }


def fingerprint_dca(lines: list[dict]) -> dict:
    return {
        line["name"]: {
            "multiplier": line["multiplier"],
            "monthly": line["monthly"],
            "weekly": line.get("weekly"),
            "paused": line["paused"],
            "action": line["action"],
        }
        for line in lines
    }


def fingerprint_build(lines: list[dict]) -> dict:
    """State-only fingerprint — amounts / progress never count as change."""
    return {
        line["name"]: {
            "state": line.get("state") or line.get("tier_label"),
        }
        for line in lines
    }


def dca_summary_line(policy: dict | None = None) -> str:
    base = portfolio_monthly_base(policy)
    cap = portfolio_monthly_cap(policy)
    return (
        f"定投：组合月基础{base:.0f}元、封顶{cap:.0f}元（低估最高倍率可上浮至封顶）；"
        f"A/美股≥90%停、80%～90%→50%；周四周报；与建仓邮件分离"
    )


def build_summary_line(policy: dict | None = None) -> str:
    cfg = bootstrap_rules(policy)
    if not cfg.get("enabled"):
        return "建仓未启用"
    days = int(cfg.get("confirm_trading_days", 2))
    return (
        f"建仓：每日刷新、状态机触发；升级/恢复需连续{days}个交易日确认；"
        "风险信号立即发；同一状态不重复催促；不与周度定投合并"
    )
