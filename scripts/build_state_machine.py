"""Build-position state machine: daily observe, notify only on confirmed change.

States (per equity sleeve):
  不可买 | 宽松观测仓 25% | 正式小额底仓 50% | 正式建仓 100%
  | QDII溢价阻断 | 止盈观察 | 数据源失败

Upgrades / recoveries need N consecutive observations (default 2 trading days).
Risk transitions (premium block, lose buyable, take-profit, data fail, tier
downgrade) notify immediately. Amount / progress changes never trigger mail.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from policy_rules import (
    US,
    bootstrap_planned_amount,
    bootstrap_rules,
    index_bootstrap_rules,
    load_policy,
    rules,
    try_bootstrap_action,
)


def _portfolio_monthly_base(policy: dict | None = None) -> float:
    policy = policy or load_policy()
    dca = policy.get("dca") or {}
    return float(dca.get("portfolio_monthly_base", dca.get("monthly_base", 300)))

CST = timezone(timedelta(hours=8))

STATE_UNBUYABLE = "不可买"
STATE_SOFT_25 = "宽松观测仓 25%"
STATE_FORMAL_50 = "正式小额底仓 50%"
STATE_FORMAL_100 = "正式建仓 100%"
STATE_PREMIUM = "QDII溢价阻断"
STATE_TAKE_PROFIT = "止盈观察"
STATE_DATA_FAIL = "数据源失败"

BUYABLE_RANK = {
    STATE_SOFT_25: 1,
    STATE_FORMAL_50: 2,
    STATE_FORMAL_100: 3,
}

RISK_IMMEDIATE = {STATE_PREMIUM, STATE_TAKE_PROFIT, STATE_DATA_FAIL}

FRAC_TO_STATE = {
    1.0: STATE_FORMAL_100,
    0.5: STATE_FORMAL_50,
    0.25: STATE_SOFT_25,
}


def empty_machine() -> dict[str, Any]:
    return {
        "current_state": None,
        "candidate_state": None,
        "candidate_count": 0,
        "last_notified_state": None,
        "last_notified_at": None,
    }


def confirm_days(policy: dict | None = None) -> int:
    cfg = bootstrap_rules(policy)
    return max(1, int(cfg.get("confirm_trading_days", 2)))


def state_from_fraction(frac: float) -> str:
    for key, state in FRAC_TO_STATE.items():
        if abs(float(frac) - key) < 1e-9:
            return state
    if frac >= 0.999:
        return STATE_FORMAL_100
    if frac >= 0.499:
        return STATE_FORMAL_50
    if frac >= 0.249:
        return STATE_SOFT_25
    return STATE_UNBUYABLE


def is_buyable(state: str | None) -> bool:
    return state in BUYABLE_RANK


def needs_immediate(from_state: str | None, to_state: str) -> bool:
    """Risk / weaken signals — notify without multi-day confirm."""
    if to_state in RISK_IMMEDIATE:
        return True
    if from_state is None:
        return False
    if is_buyable(from_state) and to_state == STATE_UNBUYABLE:
        return True
    if is_buyable(from_state) and is_buyable(to_state):
        return BUYABLE_RANK[to_state] < BUYABLE_RANK[from_state]
    return False


def needs_confirm(from_state: str | None, to_state: str) -> bool:
    """Upgrade or recovery into a buyable tier."""
    if not is_buyable(to_state):
        return False
    if from_state is None:
        return False
    if not is_buyable(from_state):
        return True
    return BUYABLE_RANK[to_state] > BUYABLE_RANK[from_state]


def observe_build_state(
    name: str,
    *,
    percentile: float | None,
    percentile_1y: float | None,
    drawdown_from_52w_high: float | None = None,
    premium: float | None = None,
    drawdown_status: str | None = None,
    pe_status: str | None = None,
    verified: bool | None = None,
    tradeable: bool | None = None,
    held_cost: float = 0.0,
    target_amount: float = 0.0,
    policy: dict | None = None,
    month_slice: float | None = None,
) -> dict[str, Any]:
    """Map market inputs → observed state (+ amount/reason for email)."""
    policy = policy or load_policy()
    base = {
        "name": name,
        "state": STATE_UNBUYABLE,
        "active": False,
        "action": "none",
        "fraction": 0.0,
        "amount": 0.0,
        "tier_label": STATE_UNBUYABLE,
        "reason": "条件未触发",
        "needs_human_confirm": True,
    }

    cfg = bootstrap_rules(policy)
    if not cfg.get("enabled", False):
        base["reason"] = "建仓未启用"
        return base
    exclude = cfg.get("exclude") or []
    if name in exclude:
        base["reason"] = "该标的不参与自动建仓"
        return base
    allow = cfg.get("indexes")
    if allow is not None and name not in allow:
        base["reason"] = "未纳入建仓观察列表"
        return base

    ic = index_bootstrap_rules(name, policy)
    r = rules(policy)
    pause_at = float(
        (policy.get("dca") or {}).get("pause_10y_at_or_above", 90)
    )
    premium_cap = ic.get("qdii_premium_at_or_below")
    if premium_cap is None and name in US:
        premium_cap = float(r.get("qdii_premium_pause_above", 0.02))

    # --- data failure (immediate risk) ---
    if pe_status in ("fetch_failed", "error") or (
        drawdown_status == "fetch_failed"
        and percentile_1y is not None
    ):
        base.update(
            {
                "state": STATE_DATA_FAIL,
                "tier_label": STATE_DATA_FAIL,
                "action": "data_fail",
                "reason": "估值/回撤数据源失败，建仓判断暂停（fail-closed）",
            }
        )
        return base

    if ic.get("require_verified") and (verified is not True or tradeable is False):
        base.update(
            {
                "state": STATE_DATA_FAIL,
                "tier_label": STATE_DATA_FAIL,
                "action": "data_fail",
                "reason": "标普估值未核验或不可交易，建仓判断暂停",
            }
        )
        return base

    if name in US and premium is None:
        base.update(
            {
                "state": STATE_DATA_FAIL,
                "tier_label": STATE_DATA_FAIL,
                "action": "data_fail",
                "reason": "QDII 溢价数据缺失，建仓判断暂停（fail-closed）",
            }
        )
        return base

    # --- QDII premium risk ---
    if name in US and premium_cap is not None and premium is not None and premium > premium_cap:
        base.update(
            {
                "state": STATE_PREMIUM,
                "tier_label": STATE_PREMIUM,
                "action": "premium_block",
                "reason": (
                    f"QDII溢价 {premium * 100:.2f}% > {premium_cap * 100:.0f}%，阻断建仓"
                ),
            }
        )
        return base

    # --- take-profit / overvalued ---
    if percentile is not None and percentile >= pause_at:
        base.update(
            {
                "state": STATE_TAKE_PROFIT,
                "tier_label": STATE_TAKE_PROFIT,
                "action": "take_profit",
                "reason": (
                    f"近10年分位 {percentile:.2f}% ≥ {pause_at:.0f}%，止盈观察；不开建仓"
                ),
            }
        )
        return base

    if percentile_1y is None:
        base.update(
            {
                "state": STATE_DATA_FAIL,
                "tier_label": STATE_DATA_FAIL,
                "action": "data_fail",
                "reason": "缺少近1年PE分位，建仓判断暂停",
            }
        )
        return base

    boot = try_bootstrap_action(
        name,
        percentile=percentile,
        percentile_1y=percentile_1y,
        drawdown_from_52w_high=drawdown_from_52w_high,
        premium=premium,
        policy=policy,
        verified=verified,
        tradeable=tradeable,
        held_cost=held_cost,
        target_amount=target_amount,
    )
    if boot is None:
        base["reason"] = "未纳入建仓或条件未触发"
        return base

    action, reason, frac = boot
    if action == "premium_block":
        base.update(
            {
                "state": STATE_PREMIUM,
                "tier_label": STATE_PREMIUM,
                "action": action,
                "reason": reason,
            }
        )
        return base

    if action in ("wait", "none") or frac <= 0:
        base.update(
            {
                "state": STATE_UNBUYABLE,
                "tier_label": STATE_UNBUYABLE,
                "action": action,
                "reason": reason,
            }
        )
        return base

    state = state_from_fraction(frac)
    slice_amt = (
        month_slice if month_slice is not None else _portfolio_monthly_base(policy)
    )
    amount = bootstrap_planned_amount(
        held_cost,
        target_amount,
        policy,
        month_slice=slice_amt,
        fraction=frac,
    )
    base.update(
        {
            "state": state,
            "tier_label": state,
            "active": True,
            "action": action,
            "fraction": frac,
            "amount": amount,
            "reason": reason,
            "needs_human_confirm": bool(
                (policy.get("guardrails") or {}).get("require_human_confirmation", True)
            ),
        }
    )
    return base


def advance_machine(
    machine: dict[str, Any] | None,
    observed_state: str,
    *,
    confirm_needed: int = 2,
    now: datetime | None = None,
    force_notify: bool = False,
) -> tuple[dict[str, Any], bool, str | None]:
    """Advance one sleeve machine.

    Returns (new_machine, should_notify, change_line).
    """
    now = now or datetime.now(CST)
    m = dict(empty_machine())
    if machine:
        m.update({k: machine.get(k) for k in empty_machine()})

    current = m.get("current_state")

    # First observation: baseline only, no email (unless force).
    if current is None:
        m["current_state"] = observed_state
        m["candidate_state"] = None
        m["candidate_count"] = 0
        if force_notify:
            m["last_notified_state"] = observed_state
            m["last_notified_at"] = now.isoformat(timespec="seconds")
            return m, True, f"（初始化）→ {observed_state}"
        m["last_notified_state"] = observed_state
        m["last_notified_at"] = now.isoformat(timespec="seconds")
        return m, False, None

    if observed_state == current:
        m["candidate_state"] = None
        m["candidate_count"] = 0
        if force_notify:
            m["last_notified_state"] = observed_state
            m["last_notified_at"] = now.isoformat(timespec="seconds")
            return m, True, f"{current}（强制）"
        return m, False, None

    change = f"{current} → {observed_state}"

    if force_notify or needs_immediate(current, observed_state):
        m["current_state"] = observed_state
        m["candidate_state"] = None
        m["candidate_count"] = 0
        m["last_notified_state"] = observed_state
        m["last_notified_at"] = now.isoformat(timespec="seconds")
        return m, True, change

    if needs_confirm(current, observed_state):
        if m.get("candidate_state") == observed_state:
            count = int(m.get("candidate_count") or 0) + 1
        else:
            count = 1
        m["candidate_state"] = observed_state
        m["candidate_count"] = count
        if count >= confirm_needed:
            m["current_state"] = observed_state
            m["candidate_state"] = None
            m["candidate_count"] = 0
            m["last_notified_state"] = observed_state
            m["last_notified_at"] = now.isoformat(timespec="seconds")
            return m, True, f"{change}（连续{confirm_needed}日确认）"
        return m, False, None

    # Same-severity non-buyable drift (e.g. 不可买 reason change): treat as immediate
    # only when state id actually differs — already handled above.
    # Fallback: accept immediately for any other transition.
    m["current_state"] = observed_state
    m["candidate_state"] = None
    m["candidate_count"] = 0
    m["last_notified_state"] = observed_state
    m["last_notified_at"] = now.isoformat(timespec="seconds")
    return m, True, change


def fingerprint_from_machines(machines: dict[str, dict]) -> dict:
    """Compat fingerprint for alert_state.build (state only — no amounts)."""
    return {
        name: {"state": (m or {}).get("current_state")}
        for name, m in machines.items()
    }
