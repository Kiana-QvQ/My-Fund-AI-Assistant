"""Persist last DCA fingerprints and build state machines for event emails."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "alert_state.json"


def load_alert_state(path: Path = STATE_PATH) -> dict:
    if not path.is_file():
        return {
            "dca": {},
            "build": {},
            "build_machines": {},
            "updated_at": None,
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_alert_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _mult_label(fp: Any) -> str:
    if not isinstance(fp, dict):
        return str(fp)
    if fp.get("paused"):
        action = str(fp.get("action") or "")
        if action == "premium_block":
            return "溢价暂停"
        if action == "reference":
            return "仅参考"
        return "暂停"
    mult = fp.get("multiplier")
    if mult is None:
        return str(fp.get("action") or "—")
    return f"{float(mult) * 100:.0f}%"


def format_dca_changes(old: dict, new: dict) -> list[str]:
    """Human-readable DCA fingerprint diffs (no raw dict dumps)."""
    changes: list[str] = []
    names = sorted(set(old) | set(new))
    for name in names:
        a = old.get(name)
        b = new.get(name)
        if a == b:
            continue
        left = _mult_label(a) if a is not None else "（无）"
        right = _mult_label(b) if b is not None else "（无）"
        # Append monthly hint when both sides are active buy fingerprints.
        extra = ""
        if (
            isinstance(a, dict)
            and isinstance(b, dict)
            and not a.get("paused")
            and not b.get("paused")
            and a.get("monthly") is not None
            and b.get("monthly") is not None
            and float(a["monthly"]) != float(b["monthly"])
        ):
            extra = f"（月额度 {float(a['monthly']):.0f}→{float(b['monthly']):.0f}）"
        changes.append(f"{name}：{left} → {right}{extra}")
    return changes


def diff_fingerprint(old: dict, new: dict) -> list[str]:
    """Backward-compatible alias — prefer format_dca_changes for DCA."""
    return format_dca_changes(old, new)
