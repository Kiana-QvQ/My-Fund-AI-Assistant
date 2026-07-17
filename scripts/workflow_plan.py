"""Resolve which portfolio-update workflow steps should run.

Mirrors the if: conditions in .github/workflows/portfolio-update.yml so tests
can simulate holiday vs trading-day paths without invoking Actions.
"""

from __future__ import annotations

from typing import Any


def plan_portfolio_update_steps(
    *,
    run: str,
    run_us: str,
    alert_mode: str,
) -> dict[str, Any]:
    """Return a boolean plan for each major step.

    alert_mode: auto | weekly_dca | event | force_dca | force_build | skip

    On A-share holidays the scheduled workflow uses alert_mode=event so dynamic
    emails can still fire; weekly DCA remains Thursday+trading-day only.
    """
    run_yes = run == "yes"
    run_us_yes = run_us == "yes"
    mode = (alert_mode or "auto").strip() or "auto"
    # Scheduled holiday path passes mode=event from the workflow YAML.
    send = (run_yes or run_us_yes) and mode != "skip"
    return {
        "refresh_full": run_yes,
        "refresh_us_only": (not run_yes) and run_us_yes,
        "update_readme": run_yes or run_us_yes,
        "commit": run_yes or run_us_yes,
        "send_email": send,
        "email_mode": mode if send else None,
        "email_slot": None,
        "holiday_notice": (not run_yes) and run_us_yes,
        "skip_notice": (not run_yes) and (not run_us_yes),
        "alert_mode": mode,
    }
