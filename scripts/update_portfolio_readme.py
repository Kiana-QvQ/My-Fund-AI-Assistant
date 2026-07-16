"""Render the dynamic portfolio section in README.md."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOLDINGS_PATH = ROOT / "config" / "portfolio_holdings.json"
SNAPSHOT_PATH = ROOT / "data" / "market_snapshot.json"
STATUS_PATH = ROOT / "data" / "portfolio_status.json"
README_PATH = ROOT / "README.md"
START = "<!-- PORTFOLIO_STATUS_START -->"
END = "<!-- PORTFOLIO_STATUS_END -->"


def write_utf8_lf(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def load_json(path: Path, default: dict) -> dict:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def money(value: float) -> str:
    return f"¥{value:,.2f}"


def build_status() -> dict:
    holdings_doc = load_json(HOLDINGS_PATH, {"holdings": []})
    snapshot = load_json(SNAPSHOT_PATH, {})
    holdings = holdings_doc.get("holdings", [])
    total_cost = sum(float(item.get("cost_basis") or 0) for item in holdings)
    as_of = date.today().isoformat()
    rows = []

    for item in holdings:
        cost = float(item.get("cost_basis") or 0)
        current_percent = cost / total_cost * 100 if total_cost else 0
        target_percent = float(item.get("target_percent") or 0)
        deviation = current_percent - target_percent
        fund = snapshot.get("funds", {}).get(item["fund_code"], {})
        purchase_status = fund.get("purchase_status", "待刷新")
        if deviation > 5:
            decision = "暂不追加"
            reason = f"当前投入占比 {current_percent:.2f}% 高于目标 {target_percent:.2f}%"
        elif purchase_status in ("开放申购", "限大额"):
            decision = "可研究买入"
            reason = f"申购状态：{purchase_status}"
        else:
            decision = "等待确认"
            reason = f"申购状态：{purchase_status}"
        rows.append(
            {
                "fund_code": item["fund_code"],
                "name": item["name"],
                "cost_basis": cost,
                "target_percent": target_percent,
                "current_percent": current_percent,
                "deviation_percent": deviation,
                "decision": decision,
                "reason": reason,
                "nav": fund.get("nav"),
                "nav_date": fund.get("nav_date"),
            }
        )

    allocations = snapshot.get("build_plan", {}).get("allocations", [])
    for allocation in allocations:
        if allocation["fund_code"] in {row["fund_code"] for row in rows}:
            continue
        if allocation["action"] in ("buy", "double"):
            rows.append(
                {
                    "fund_code": allocation["fund_code"],
                    "name": allocation["name"],
                    "cost_basis": 0,
                    "target_percent": allocation["target_percent"],
                    "current_percent": 0,
                    "deviation_percent": -allocation["target_percent"],
                    "decision": "可研究买入" if allocation["action"] == "buy" else "可研究加倍",
                    "reason": allocation["reason"],
                    "nav": None,
                    "nav_date": None,
                }
            )

    if rows:
        held = rows[0]
        if held["decision"] == "暂不追加":
            overall = f"🟡 今日建议：暂不追加 {held['fund_code']}，先观察其他目标资产估值"
        else:
            overall = "🟢 今日建议：存在符合策略的研究买入项，下单前人工核对"
    else:
        overall = "⚪ 今日建议：等待行情快照"

    status = {
        "as_of": as_of,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "total_cost_basis": total_cost,
        "overall_decision": overall,
        "rows": rows,
        "data_status": "market_snapshot.json 已加载"
        if snapshot
        else "尚未生成 market_snapshot.json",
    }
    return status


def render(status: dict) -> str:
    lines = [
        START,
        f"> 自动更新时间：**{status['as_of']}**",
        f"> 已记录投入总额：**{money(status['total_cost_basis'])}**",
        f"> {status['overall_decision']}",
        "> 说明：当前投入占比 = 单项已投入金额 ÷ 已记录持仓投入总额；不是券商账户实时市值占比。",
        "",
        "| 基金 | 代码 | 已投入 | 目标仓位 | 当前投入占比 | 偏离目标 | 今日建议 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in status["rows"]:
        lines.append(
            f"| {row['name']} | `{row['fund_code']}` | "
            f"{money(row['cost_basis'])} | {row['target_percent']:.2f}% | "
            f"**{row['current_percent']:.2f}%** | "
            f"{row['deviation_percent']:+.2f}% | {row['decision']} |"
        )
    lines.extend(
        [
            "",
            "### 今日判断依据",
            "",
        ]
    )
    for row in status["rows"]:
        lines.append(f"- `{row['fund_code']}`：{row['reason']}。")
    lines.extend(
        [
            "",
            f"> 数据状态：{status['data_status']}。AI 只提供研究建议，不自动下单。",
            END,
        ]
    )
    return "\n".join(lines)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    status = build_status()
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_utf8_lf(
        STATUS_PATH,
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
    )
    original = README_PATH.read_text(encoding="utf-8")
    if START in original and END in original:
        before, remainder = original.split(START, 1)
        _, after = remainder.split(END, 1)
        updated = before.rstrip() + "\n\n" + render(status) + "\n" + after.lstrip()
    else:
        marker = "\n## 当前持仓\n"
        insert_at = original.index(marker) if marker in original else len(original)
        updated = original[:insert_at] + "\n" + render(status) + "\n" + original[insert_at:]
    write_utf8_lf(README_PATH, updated)
    print(f"updated {README_PATH}")
    print(status["overall_decision"])


if __name__ == "__main__":
    main()
