"""DCA (weekly), personal schedules, and build (event) plan helpers.

Portfolio budget model:
- monthly_base / monthly_cap apply to the **whole plan** (default 300 / 1000)
- Split across 5 sleeves by target_allocation weights
- Paused / excluded equity weight flows to 短债
- Weekly size = remaining month budget ÷ remaining Thursdays (enforces month cap)
- Personal schedules are tracked separately from the portfolio budget
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


def personal_dca_schedules(policy: dict | None = None) -> dict[str, dict]:
    """Return user-defined schedules that do not use the portfolio budget."""
    schedules = dca_config(policy).get("personal_schedules") or {}
    return {
        str(code): dict(schedule)
        for code, schedule in schedules.items()
        if isinstance(schedule, dict)
    }


def personal_dca_schedule(
    fund_code: str,
    policy: dict | None = None,
) -> dict | None:
    return personal_dca_schedules(policy).get(str(fund_code))


def personal_dca_fund_codes(policy: dict | None = None) -> set[str]:
    return set(personal_dca_schedules(policy))


def personal_dca_line(
    fund_code: str,
    *,
    month_spent: float = 0.0,
    is_trading_day: bool = True,
    policy: dict | None = None,
) -> dict:
    """Calculate one personal trading-day DCA installment.

    This is intentionally separate from the valuation-based weekly portfolio
    plan: the user's broker schedule is recorded, while the system's risk
    signals remain advisory and fail-closed.
    """
    schedule = personal_dca_schedule(fund_code, policy)
    code = str(fund_code)
    if schedule is None:
        return {
            "fund_code": code,
            "enabled": False,
            "daily_amount": 0.0,
            "monthly_stop_amount": 0.0,
            "month_spent": round(max(0.0, float(month_spent or 0.0)), 2),
            "month_remaining": 0.0,
            "today_amount": 0.0,
            "executable": False,
            "reason": "未配置个人定投计划",
        }

    daily = max(0.0, float(schedule.get("daily_amount") or 0.0))
    stop_amount = max(
        0.0,
        float(
            schedule.get(
                "monthly_stop_amount",
                schedule.get("monthly_cap", 0.0),
            )
            or 0.0
        ),
    )
    spent = max(0.0, float(month_spent or 0.0))
    remaining = round(max(0.0, stop_amount - spent), 2)
    enabled = bool(schedule.get("enabled", True))
    trading_days_only = bool(schedule.get("trading_days_only", True))
    can_run = enabled and (is_trading_day or not trading_days_only) and remaining > 0
    today_amount = round(min(daily, remaining), 2) if can_run else 0.0

    if not enabled:
        reason = "个人定投已关闭"
    elif trading_days_only and not is_trading_day:
        reason = "今天非交易日，跳过本次定投"
    elif remaining <= 0:
        reason = f"本月已达到终止金额 {stop_amount:.0f} 元"
    else:
        reason = (
            f"交易日定投 {daily:.0f} 元；本月累计达到 {stop_amount:.0f} 元后停止"
        )

    return {
        "name": schedule.get("name") or code,
        "fund_code": code,
        "schedule": schedule.get("schedule") or "trading_day",
        "trading_days_only": trading_days_only,
        "enabled": enabled,
        "daily_amount": daily,
        "monthly_stop_amount": stop_amount,
        "month_spent": round(spent, 2),
        "month_remaining": remaining,
        "today_amount": today_amount,
        "executable": today_amount > 0,
        "reason": reason,
    }


def personal_dca_summary_line(policy: dict | None = None) -> str:
    schedules = personal_dca_schedules(policy)
    if not schedules:
        return "个人定投：未配置独立计划"
    parts = []
    for schedule in schedules.values():
        if not schedule.get("enabled", True):
            continue
        cadence = (
            "交易日"
            if schedule.get("trading_days_only", True)
            else "每日"
        )
        daily = float(schedule.get("daily_amount") or 0)
        stop_amount = float(
            schedule.get(
                "monthly_stop_amount",
                schedule.get("monthly_cap", 0),
            )
            or 0
        )
        parts.append(
            f"{schedule.get('name') or schedule.get('fund_code')} {cadence}{daily:.0f}元、"
            f"月终止{stop_amount:.0f}元"
        )
    return "个人定投：" + "；".join(parts) + "（不计入组合周度预算）"


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


def apply_avg_cost_soft_gates(
    lines: list[dict],
    snapshot: dict | None,
    *,
    policy: dict | None = None,
    holdings_doc: dict | None = None,
    min_equity_weekly: float | None = None,
) -> tuple[list[dict], float]:
    """Soft-scale equity weekly amounts; return (lines, residual_to_short_bond).

    Soft-cut and post-scale tiny sleeves are folded into residual (短债).
    """
    from cost_basis_metrics import (  # noqa: WPS433
        apply_soft_buy_scale,
        holding_cost_metrics,
        holdings_by_code,
    )
    from fund_purchase_gate import fund_record  # noqa: WPS433
    from record_holding import load_holdings  # noqa: WPS433

    policy = policy or load_policy()
    cfg = dca_config(policy)
    min_equity = float(
        min_equity_weekly
        if min_equity_weekly is not None
        else cfg.get("min_equity_weekly_amount", 10)
    )
    held = holdings_by_code(holdings_doc or load_holdings())
    out: list[dict] = []
    residual_extra = 0.0
    for line in lines:
        row = dict(line)
        code = str(row.get("fund_code") or "")
        weekly = float(row.get("weekly") or 0)
        if row.get("role") != "equity":
            out.append(row)
            continue

        fund = fund_record(snapshot, code)
        metrics = holding_cost_metrics(held.get(code), fund.get("nav"))
        row["avg_cost"] = metrics.get("avg_cost")
        row["vs_avg_pct"] = metrics.get("vs_avg_pct")
        row["holding_shares"] = metrics.get("shares")
        row["nav"] = metrics.get("nav")

        if weekly <= 0:
            out.append(row)
            continue

        new_amt, reason, scale = apply_soft_buy_scale(
            weekly,
            fund_code=code,
            vs_avg_percent=metrics.get("vs_avg_pct"),
            policy=policy,
            is_personal_dca=False,
        )
        row["avg_cost_scale"] = scale
        if reason and new_amt != weekly:
            cut = round(weekly - new_amt, 2)
            if cut > 0:
                residual_extra = round(residual_extra + cut, 2)
            row["weekly"] = new_amt
            row["reason"] = f"{row.get('reason') or ''}；{reason}".lstrip("；")
            weekly = new_amt

        if 0 < weekly < min_equity:
            residual_extra = round(residual_extra + weekly, 2)
            row["weekly"] = 0.0
            row["monthly"] = 0.0
            row["paused"] = True
            row["action"] = "avg_cost_soft" if reason else "wait"
            row["reason"] = (
                f"{row.get('reason') or ''}；软降后本周 {weekly:.2f} 元 < "
                f"{min_equity:.0f} 元，并入短债".lstrip("；")
            )
            row["executable"] = False
            out.append(row)
            continue

        if weekly <= 0:
            row["weekly"] = 0.0
            row["monthly"] = 0.0
            row["paused"] = True
            row["executable"] = False
            row["action"] = "avg_cost_soft"
        out.append(row)
    return out, residual_extra


def apply_dca_purchase_gates(
    lines: list[dict],
    snapshot: dict | None,
    *,
    policy: dict | None = None,
) -> list[dict]:
    """Zero blocked buys and fold tiny equity weeks into 短债.

    Gates: purchase_status / daily_limit / minimum_purchase.
    Equity weekly below min_equity_weekly_amount → merge into residual.
    Avg-cost soft gate runs after purchase gates; soft-cut also folds to 短债.
    """
    from fund_purchase_gate import (  # noqa: WPS433
        apply_gate_to_amount,
        attach_fund_meta,
        fund_record,
    )

    policy = policy or load_policy()
    cfg = dca_config(policy)
    min_equity = float(cfg.get("min_equity_weekly_amount", 10))
    residual_extra = 0.0
    out: list[dict] = []

    for line in lines:
        row = dict(line)
        code = str(row.get("fund_code") or "")
        fund = fund_record(snapshot, code)
        attach_fund_meta(row, fund)
        role = row.get("role")
        weekly = float(row.get("weekly") or 0)

        if weekly <= 0:
            out.append(row)
            continue

        # Tiny equity sleeves: merge into short bond for bank UX
        if role == "equity" and 0 < weekly < min_equity:
            residual_extra = round(residual_extra + weekly, 2)
            row["weekly"] = 0.0
            row["monthly"] = 0.0
            row["paused"] = True
            row["action"] = "wait"
            row["reason"] = (
                f"{row.get('reason') or ''}；本周 {weekly:.2f} 元 < {min_equity:.0f} 元，"
                f"并入短债".lstrip("；")
            )
            row["executable"] = False
            out.append(row)
            continue

        new_amt, block = apply_gate_to_amount(weekly, fund)
        if block:
            # Equity/residual blocked → redirect equity to residual; residual stays 0
            if role == "equity":
                residual_extra = round(residual_extra + weekly, 2)
            row["weekly"] = 0.0
            row["monthly"] = 0.0
            row["paused"] = True
            row["action"] = "purchase_block"
            row["reason"] = (
                f"{row.get('reason') or ''}；{block}".lstrip("；")
            )
            row["executable"] = False
            out.append(row)
            continue

        row["executable"] = True
        row["weekly"] = new_amt
        out.append(row)

    out, soft_residual = apply_avg_cost_soft_gates(
        out, snapshot, policy=policy, min_equity_weekly=min_equity
    )
    residual_extra = round(residual_extra + soft_residual, 2)

    if residual_extra > 0:
        for row in out:
            if row.get("role") == "residual":
                fund = fund_record(snapshot, str(row.get("fund_code") or ""))
                candidate = round(float(row.get("weekly") or 0) + residual_extra, 2)
                new_amt, block = apply_gate_to_amount(candidate, fund)
                if block:
                    # Cannot park in short bond either — leave extra unallocated
                    row["reason"] = (
                        f"{row.get('reason') or ''}；拟转入 {residual_extra:.2f} 元但短债"
                        f"不可申购：{block}".lstrip("；")
                    )
                    row["paused"] = True
                    row["executable"] = False
                    row["weekly"] = 0.0
                else:
                    row["weekly"] = new_amt
                    row["paused"] = new_amt <= 0
                    row["executable"] = new_amt > 0
                    row["action"] = "buy" if new_amt > 0 else "wait"
                    row["reason"] = (
                        f"{row.get('reason') or ''}；含权益暂停/小额并入 "
                        f"{residual_extra:.2f} 元".lstrip("；")
                    )
                    attach_fund_meta(row, fund)
                break

    return out


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
        f"A/美股≥90%停、80%～90%→50%；周四周报；与建仓邮件分离；"
        f"{personal_dca_summary_line(policy)}"
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
