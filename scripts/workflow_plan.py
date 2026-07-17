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

    Parameters use the same yes/no strings written to GITHUB_OUTPUT.
    """
    run_yes = run == "yes"
    run_us_yes = run_us == "yes"
    mode = (alert_mode or "auto").strip() or "auto"
    return {
        "refresh_full": run_yes,
        "refresh_us_only": (not run_yes) and run_us_yes,
        "update_readme": run_yes or run_us_yes,
        "commit": run_yes or run_us_yes,
        "send_email": run_yes and mode != "skip",
        "holiday_notice": (not run_yes) and run_us_yes,
        "skip_notice": (not run_yes) and (not run_us_yes),
        "alert_mode": mode,
    }
