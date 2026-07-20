"""Fund purchase gate: bank can buy only if status/limit/min allow.

Used by DCA allocation and build emails so signals match OTC reality.
"""

from __future__ import annotations

from typing import Any


ALLOWED_PURCHASE_STATUS = ("开放申购", "限大额")


def fund_record(snapshot: dict | None, fund_code: str | None) -> dict[str, Any]:
    if not snapshot or not fund_code:
        return {}
    funds = snapshot.get("funds") or {}
    return dict(funds.get(str(fund_code)) or {})


def purchase_gate(
    amount: float,
    *,
    purchase_status: str | None = None,
    daily_limit: float | None = None,
    minimum_purchase: float | None = None,
    status_missing_fail_closed: bool = True,
) -> tuple[bool, str]:
    """Return (ok, reason). ok=False means do not recommend this buy amount."""
    amt = float(amount or 0)
    if amt <= 0:
        return True, ""

    status = (purchase_status or "").strip()
    if not status:
        if status_missing_fail_closed:
            return False, "申购状态缺失，暂停建议买入（fail-closed）"
        return True, ""

    if status == "暂停申购":
        return False, "基金当前暂停申购"
    if status not in ALLOWED_PURCHASE_STATUS:
        return False, f"申购状态为 {status}"

    min_buy = float(minimum_purchase) if minimum_purchase is not None else None
    if min_buy is not None and min_buy > 0 and amt + 1e-9 < min_buy:
        return False, f"建议金额 {amt:.2f} 元低于购买起点 {min_buy:.0f} 元"

    limit = float(daily_limit) if daily_limit is not None else None
    # Treat huge limits as unlimited; tiny limits (e.g. 10) are real caps.
    if limit is not None and limit > 0 and limit < 1e8 and amt > limit + 1e-9:
        return False, f"建议金额 {amt:.2f} 元超过日限额 {limit:.0f} 元"

    return True, ""


def attach_fund_meta(line: dict, fund: dict | None) -> dict:
    """Copy purchase fields onto a plan line for emails."""
    fund = fund or {}
    line["purchase_status"] = fund.get("purchase_status")
    line["daily_limit"] = fund.get("daily_limit")
    line["minimum_purchase"] = fund.get("minimum_purchase")
    return line


def apply_gate_to_amount(
    amount: float,
    fund: dict | None,
    *,
    status_missing_fail_closed: bool = True,
) -> tuple[float, str | None]:
    """Clamp or zero amount per fund gate. Returns (new_amount, block_reason|None)."""
    fund = fund or {}
    ok, reason = purchase_gate(
        amount,
        purchase_status=fund.get("purchase_status"),
        daily_limit=fund.get("daily_limit"),
        minimum_purchase=fund.get("minimum_purchase"),
        status_missing_fail_closed=status_missing_fail_closed,
    )
    if ok:
        # If limited and amount fits, keep; if over limit we already failed.
        # Optionally cap to daily_limit when over — fail-closed prefers block.
        return float(amount or 0), None
    return 0.0, reason
