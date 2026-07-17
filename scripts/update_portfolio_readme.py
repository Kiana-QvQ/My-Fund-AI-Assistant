"""Render the dynamic portfolio section in README.md."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo

    CST = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - environments without tzdata
    CST = timezone(timedelta(hours=8))


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


def now_cst() -> datetime:
    return datetime.now(CST)


def format_update_time(when: datetime) -> str:
    local = when.astimezone(CST) if when.tzinfo else when.replace(tzinfo=CST)
    return local.strftime("%Y-%m-%d %H:%M:%S CST")


def summarize_equity(indexes: dict) -> tuple[str, list[str]]:
    """Return overall equity tone and short per-index notes."""
    rules = (
        ("沪深300", 40.0, "A股"),
        ("中证500", 40.0, "A股"),
        ("标普500", 50.0, "美股"),
        ("纳斯达克100", 50.0, "美股"),
    )
    buyable: list[str] = []
    paused: list[str] = []
    missing: list[str] = []
    notes: list[str] = []
    for name, threshold, _market in rules:
        item = indexes.get(name, {})
        percentile = item.get("pe_percentile")
        if percentile is None:
            missing.append(name)
            notes.append(f"{name}数据不足")
            continue
        if percentile < threshold:
            buyable.append(name)
            notes.append(f"{name}可买({percentile:.1f}%<{threshold:.0f}%)")
        else:
            paused.append(name)
            notes.append(f"{name}暂停({percentile:.1f}%≥{threshold:.0f}%)")
    if buyable:
        tone = "🟢 权益有可买信号"
    elif missing and not paused:
        tone = "⚪ 权益数据不足"
    else:
        tone = "🟡 权益均暂停新增"
    return tone, notes


def build_status() -> dict:
    holdings_doc = load_json(HOLDINGS_PATH, {"holdings": []})
    snapshot = load_json(SNAPSHOT_PATH, {})
    holdings = holdings_doc.get("holdings", [])
    total_cost = sum(float(item.get("cost_basis") or 0) for item in holdings)
    building_principal = float(
        holdings_doc.get("building_principal") or total_cost or 0
    )
    initial_build_percent = float(holdings_doc.get("initial_build_percent") or 20)
    first_month_budget = building_principal * initial_build_percent / 100
    generated = now_cst()
    as_of = generated.date().isoformat()
    rows = []
    indexes = snapshot.get("indexes", {})

    for item in holdings:
        cost = float(item.get("cost_basis") or 0)
        current_percent = cost / building_principal * 100 if building_principal else 0
        target_percent = float(item.get("target_percent") or 0)
        target_amount = building_principal * target_percent / 100
        shortfall = max(target_amount - cost, 0)
        deviation = current_percent - target_percent
        phase_target = first_month_budget * target_percent / 100
        fund = snapshot.get("funds", {}).get(item["fund_code"], {})
        purchase_status = fund.get("purchase_status", "待刷新")
        if item.get("asset_class") == "短债基金" and cost >= phase_target:
            decision = "本期不补满"
            reason = (
                f"短债不看PE；第1月计划金额约 {money(phase_target)}，"
                f"当前已投入 {money(cost)}，不建议今天一次补足 {money(shortfall)}"
            )
        elif shortfall > 0 and purchase_status in ("开放申购", "限大额"):
            decision = "目标未完成"
            reason = (
                f"目标金额 {money(target_amount)}，已投入 {money(cost)}，"
                f"还差 {money(shortfall)}；申购状态：{purchase_status}"
            )
        elif shortfall > 0:
            decision = "等待确认"
            reason = (
                f"目标金额 {money(target_amount)}，已投入 {money(cost)}，"
                f"还差 {money(shortfall)}；申购状态：{purchase_status}"
            )
        elif purchase_status in ("开放申购", "限大额"):
            decision = "已达到目标"
            reason = f"已达到目标金额 {money(target_amount)}，不建议继续追加"
        else:
            decision = "等待确认"
            reason = f"申购状态：{purchase_status}"
        rows.append(
            {
                "fund_code": item["fund_code"],
                "name": item["name"],
                "cost_basis": cost,
                "target_percent": target_percent,
                "target_amount": target_amount,
                "current_percent": current_percent,
                "deviation_percent": deviation,
                "shortfall": shortfall,
                "decision": decision,
                "reason": reason,
                "nav": fund.get("nav"),
                "nav_date": fund.get("nav_date"),
            }
        )

    allocations = snapshot.get("build_plan", {}).get("allocations", [])
    held_codes = {row["fund_code"] for row in rows}
    for allocation in allocations:
        if allocation["fund_code"] in held_codes:
            continue
        if allocation["action"] in ("buy", "double"):
            rows.append(
                {
                    "fund_code": allocation["fund_code"],
                    "name": allocation["name"],
                    "cost_basis": 0,
                    "target_percent": allocation["target_percent"],
                    "target_amount": building_principal
                    * allocation["target_percent"]
                    / 100,
                    "current_percent": 0,
                    "deviation_percent": -allocation["target_percent"],
                    "shortfall": building_principal
                    * allocation["target_percent"]
                    / 100,
                    "decision": "可研究买入"
                    if allocation["action"] == "buy"
                    else "可研究加倍",
                    "reason": allocation["reason"],
                    "nav": None,
                    "nav_date": None,
                }
            )

    equity_tone, equity_notes = summarize_equity(indexes)
    short_row = next(
        (row for row in rows if row["fund_code"] == "012773"),
        None,
    )
    if short_row and short_row["decision"] == "本期不补满":
        short_note = (
            f"短债本期不催补（{short_row['fund_code']} 长期还差 "
            f"{money(short_row['shortfall'])}）"
        )
    elif short_row and short_row["shortfall"] > 0:
        short_note = (
            f"短债距目标还差 {money(short_row['shortfall'])}"
        )
    else:
        short_note = "短债按计划持有"
    overall = f"{equity_tone}；{short_note}"

    us_as_of = None
    for name in ("标普500", "纳斯达克100"):
        us_as_of = indexes.get(name, {}).get("date") or us_as_of
    data_status = (
        "market_snapshot.json 已加载" if snapshot else "尚未生成 market_snapshot.json"
    )
    if us_as_of and us_as_of < as_of:
        data_status += f"；美股PE手工快照日期 {us_as_of}，可能滞后"

    status = {
        "as_of": as_of,
        "generated_at": generated.isoformat(timespec="seconds"),
        "updated_at_display": format_update_time(generated),
        "total_cost_basis": total_cost,
        "building_principal": building_principal,
        "initial_build_percent": initial_build_percent,
        "first_month_budget": first_month_budget,
        "building_progress_percent": total_cost / building_principal * 100
        if building_principal
        else 0,
        "overall_decision": overall,
        "equity_notes": equity_notes,
        "rows": rows,
        "indexes": indexes,
        "data_status": data_status,
    }
    return status


def render(status: dict) -> str:
    update_time = status.get("updated_at_display") or status.get("as_of", "")
    lines = [
        START,
        f"> 自动更新时间：**{update_time}**",
        f"> 建仓本金：**{money(status['building_principal'])}** · "
        f"已投入：**{money(status['total_cost_basis'])}** · "
        f"整体建仓进度：**{status['building_progress_percent']:.2f}%**",
        f"> {status['overall_decision']}",
        "> 状态灯：🟢 可买/可建仓 · 🟡 观望/暂停/不催补 · ⚪ 等待数据",
        "> 说明：当前投入占比 = 单项已投入金额 ÷ 1万元建仓本金；目标金额 = 建仓本金 × 目标仓位。",
        "",
        "| 基金 | 代码 | 已投入 | 目标仓位 | 目标金额 | 当前投入占比 | 还差目标金额 | 今日状态 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in status["rows"]:
        lines.append(
            f"| {row['name']} | `{row['fund_code']}` | "
            f"{money(row['cost_basis'])} | {row['target_percent']:.2f}% | "
            f"{money(row['target_amount'])} | **{row['current_percent']:.2f}%** | "
            f"{money(row['shortfall'])} | {row['decision']} |"
        )
    equity_notes = status.get("equity_notes") or []
    if equity_notes:
        lines.extend(["", "### 权益信号速览", ""])
        for note in equity_notes:
            lines.append(f"- {note}")
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
            "## 今日权益估值（4支）",
            "",
            "> PE 数据用于判断指数贵不贵；场外基金按当日净值成交，数据日期以指数实际更新日为准。",
            "",
            "| 标的 | 场内代码 | 场外基金 | PE-TTM | 历史分位 | 数据日期 | 今日判断 |",
            "|---|---:|---:|---:|---:|---|---|",
        ]
    )
    index_rows = (
        ("沪深300", "510300", "460300", "A股规则"),
        ("中证500", "510500", "160119", "A股规则"),
        ("标普500", "513500", "050025", "美股规则"),
        ("纳斯达克100", "159941", "016452", "美股规则"),
    )
    for name, market_code, fund_code, rule in index_rows:
        index = status["indexes"].get(name, {})
        pe = index.get("pe_ttm")
        percentile = index.get("pe_percentile")
        data_date = index.get("date", "待核验")
        if pe is None or percentile is None:
            pe_text = "待核验"
            percentile_text = "待核验"
            decision = "数据不足，暂停自动买入"
        elif rule == "A股规则":
            pe_text = f"{pe:.2f}"
            percentile_text = f"{percentile:.2f}%"
            if percentile <= 30:
                decision = "低估，可研究双倍定投"
            elif percentile < 40:
                decision = "低估，可研究定投"
            else:
                decision = "分位≥40%，暂停新增"
        else:
            pe_text = f"{pe:.2f}"
            percentile_text = f"{percentile:.2f}%"
            decision = (
                "分位<50%，可研究定投"
                if percentile < 50
                else "分位≥50%，暂停新增"
            )
        lines.append(
            f"| {name} | `{market_code}` | `{fund_code}` | {pe_text} | "
            f"{percentile_text} | {data_date} | {decision} |"
        )
    lines.extend(
        [
            "",
            "> A股分位为近10年滚动PE；美股PE来自 `config/us_pe_snapshot.json`（需手工更新）。不同网站口径可能不同。",
        ]
    )
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
