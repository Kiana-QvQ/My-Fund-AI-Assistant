"""Persist last DCA fingerprints and build state machines for event emails."""

from __future__ import annotations

import json
from pathlib import Path


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


def diff_fingerprint(old: dict, new: dict) -> list[str]:
    """Return human-readable change lines."""
    changes: list[str] = []
    names = sorted(set(old) | set(new))
    for name in names:
        a = old.get(name)
        b = new.get(name)
        if a == b:
            continue
        changes.append(f"{name}: {a} → {b}")
    return changes
