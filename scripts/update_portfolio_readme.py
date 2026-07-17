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
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from investment_plan import build_summary_line, dca_summary_line  # noqa: E402
from policy_rules import (  # noqa: E402
    decision_label,
    load_policy,
    resolve_action,
)

HOLDINGS_PATH = ROOT / "config" / "portfolio_holdings.json"
SNAPSHOT_PATH = ROOT / "data" / "market_snapshot.json"
STATUS_PATH = ROOT / "data" / "portfolio_status.json"
README_PATH = ROOT / "README.md"
START = "<!-- PORTFOLIO_STATUS_START -->"
END = "<!-- PORTFOLIO_STATUS_END -->"

INDEX_WEIGHT = {
    "沪深300": ("460300", 0.27),
    "中证500": ("160119", 0.11),
    "标普500": ("050025", 0.08),
    "纳斯达克100": ("016452", 0.03),
}


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


def summarize_equity(indexes: dict, holdings_cost: dict[str, float], principal: float) -> tuple[str, list[str]]:
    """Return overall equity tone and short per-index notes."""
    names = ("沪深300", "中证500", "标普500", "纳斯达克100")
    policy = load_policy()
    buyable: list[str] = []
    half: list[str] = []
    bootstrap: list[str] = []
    take_profit: list[str] = []
    blocked: list[str] = []
    paused: list[str] = []
    missing: list[str] = []
    notes: list[str] = []
    for name in names:
        item = indexes.get(name, {})
        code, weight = INDEX_WEIGHT[name]
        action, reason = resolve_action(
            name,
            item.get("pe_percentile"),
            percentile_1y=item.get("pe_percentile_1y"),
            drawdown_from_52w_high=item.get("drawdown_from_52w_high"),
            premium=item.get("qdii_premium"),
            policy=policy,
            verified=item.get("verified"),
            tradeable=item.get("tradeable"),
            held_cost=float(holdings_cost.get(code, 0) or 0),
            target_amount=principal * weight,
        )
        label = decision_label(action)
        if action == "overvalued_watch":
            label = "高估观察，当前无持仓无需止盈"
        premium_pct = item.get("qdii_premium_pct")
        pct_1y = item.get("pe_percentile_1y")
        dd_pct = item.get("drawdown_from_52w_high_pct")
        suffix = (
            f"，溢价{premium_pct:.2f}%"
            if isinstance(premium_pct, (int, float))
            else ""
        )
        if isinstance(pct_1y, (int, float)):
            suffix += f"，1年分位{pct_1y:.1f}%"
        elif name != "纳斯达克100" and item.get("reference_only"):
            pass
        elif name == "纳斯达克100" and pct_1y is None:
            suffix += "，1年分位无统计"
        if isinstance(dd_pct, (int, float)):
            suffix += f"，52周回撤{dd_pct:.1f}%"
        notes.append(f"{name}：{label}{suffix}")
        if action == "bootstrap":
            bootstrap.append(name)
        elif action in ("buy", "double", "triple", "boost_150", "three_quarter", "seventy"):
            buyable.append(name)
        elif action == "half":
            half.append(name)
        elif action == "take_profit":
            take_profit.append(name)
        elif action == "premium_block":
            blocked.append(name)
        elif action == "unknown":
            missing.append(name)
        else:
            # reference / wait / overvalued_watch
            paused.append(name)
    if buyable or bootstrap:
        tone = "🟢 权益有定投信号"
    elif half:
        tone = "🟢 权益半额定投"
    elif take_profit:
        tone = "🟠 权益进入止盈观察"
    elif blocked:
        tone = "🟡 QDII溢价过高暂缓"
    elif missing and not paused:
        tone = "⚪ 权益数据不足"
    else:
        tone = "🟡 权益均暂停新增/高估观察"
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
    allocation_by_code = {row["fund_code"]: row for row in allocations}
    for row in rows:
        alloc = allocation_by_code.get(row["fund_code"])
        if alloc and alloc["action"] == "take_profit":
            row["decision"] = "建议分批止盈"
            row["reason"] = alloc["reason"]

    held_codes = {row["fund_code"] for row in rows}
    for allocation in allocations:
        if allocation["fund_code"] in held_codes:
            continue
        if allocation["action"] in (
            "buy",
            "double",
            "triple",
            "boost_150",
            "seventy",
            "three_quarter",
            "half",
            "bootstrap",
            "take_profit",
        ):
            decision = {
                "buy": "定投100%",
                "triple": "定投300%",
                "double": "定投200%",
                "boost_150": "定投150%",
                "seventy": "定投70%",
                "three_quarter": "定投75%",
                "half": "定投50%",
                "bootstrap": "定投25%",
                "take_profit": "建议分批止盈",
            }[allocation["action"]]
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
                    "decision": decision,
                    "reason": allocation["reason"],
                    "nav": None,
                    "nav_date": None,
                }
            )

    equity_tone, equity_notes = summarize_equity(
        indexes,
        {
            item["fund_code"]: float(item.get("cost_basis") or 0)
            for item in holdings
        },
        building_principal,
    )
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

    spx = indexes.get("标普500", {})
    ndx = indexes.get("纳斯达克100", {})
    data_status = (
        "market_snapshot.json 已加载" if snapshot else "尚未生成 market_snapshot.json"
    )
    if spx.get("verified") is True:
        data_status += f"；标普PE已核验（Multpl，{spx.get('date', '-')}）"
    elif spx:
        data_status += "；标普PE未核验/校验失败（禁止自动买入）"
    if ndx.get("reference_only") and isinstance(ndx.get("pe_ttm"), (int, float)):
        data_status += f"；纳指参考PE {ndx['pe_ttm']:.2f}（QQQ，不交易）"
    elif ndx.get("verified") is not True:
        data_status += "；纳指估值未核验/仅参考"

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
        "holdings_cost": {
            item["fund_code"]: float(item.get("cost_basis") or 0)
            for item in holdings
        },
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
        "> 状态灯：🟢 可买/微建仓/可建仓 · 🟠 止盈观察 · 🟡 观望/暂停/溢价暂缓 · ⚪ 等待数据",
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
            "| 标的 | 场内代码 | 场外基金 | PE-TTM | 10年分位 | 1年分位 | 52周回撤 | QDII溢价 | 数据日期 | 今日判断 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    index_rows = (
        ("沪深300", "510300", "460300"),
        ("中证500", "510500", "160119"),
        ("标普500", "513500", "050025"),
        ("纳斯达克100", "159941", "016452"),
    )
    policy = load_policy()
    holdings_cost = status.get("holdings_cost") or {}
    principal = float(status.get("building_principal") or 10000)
    for name, market_code, fund_code in index_rows:
        index = status["indexes"].get(name, {})
        pe = index.get("pe_ttm")
        percentile = index.get("pe_percentile")
        percentile_1y = index.get("pe_percentile_1y")
        data_date = index.get("date", "待核验")
        premium_pct = index.get("qdii_premium_pct")
        dd_pct = index.get("drawdown_from_52w_high_pct")
        if name in ("沪深300", "中证500"):
            premium_text = "-"
        elif isinstance(premium_pct, (int, float)):
            premium_text = f"{premium_pct:.2f}%"
        else:
            premium_text = "待核验"
        dd_text = f"{dd_pct:.2f}%" if isinstance(dd_pct, (int, float)) else "-"
        code, weight = INDEX_WEIGHT[name]
        held = float(holdings_cost.get(code, 0) or 0)
        action, reason = resolve_action(
            name,
            percentile,
            percentile_1y=percentile_1y,
            drawdown_from_52w_high=index.get("drawdown_from_52w_high"),
            premium=index.get("qdii_premium"),
            policy=policy,
            verified=index.get("verified"),
            tradeable=index.get("tradeable"),
            held_cost=held,
            target_amount=principal * weight,
        )
        pe_text = f"{pe:.2f}" if isinstance(pe, (int, float)) else "-"
        if name == "纳斯达克100" or index.get("reference_only"):
            percentile_text = (
                f"{percentile:.2f}%"
                if isinstance(percentile, (int, float))
                else "无统计分位"
            )
            percentile_1y_text = (
                f"{percentile_1y:.2f}%"
                if isinstance(percentile_1y, (int, float))
                else "无统计分位"
            )
            if not data_date or data_date == "待核验":
                data_date = index.get("date") or status.get("as_of") or "-"
            if isinstance(premium_pct, (int, float)):
                premium_text = f"{premium_pct:.2f}%"
        else:
            percentile_text = (
                f"{percentile:.2f}%" if isinstance(percentile, (int, float)) else "-"
            )
            percentile_1y_text = (
                f"{percentile_1y:.2f}%"
                if isinstance(percentile_1y, (int, float))
                else "-"
            )
        decision = decision_label(action)
        if action == "reference" or name == "纳斯达克100":
            decision = "仅参考·不自动买"
        elif action == "overvalued_watch":
            decision = "高估观察，当前无持仓无需止盈"
        elif action == "unknown":
            if index.get("verified") is not True:
                decision = "未核验/校验失败，禁止自动买入"
            else:
                decision = "数据不足，暂停自动买入"
        lines.append(
            f"| {name} | `{market_code}` | `{fund_code}` | {pe_text} | "
            f"{percentile_text} | {percentile_1y_text} | {dd_text} | {premium_text} | "
            f"{data_date} | {decision} |"
        )
    lines.extend(
        [
            "",
            f">{dca_summary_line(load_policy())}。"
            f"{build_summary_line(load_policy())}。"
            "回撤很深且十年已在停买区则只观察、不因跌幅抄底；**指数绝对点位不单独触发买入**。"
            "标普用 Multpl 指数PE，四层校验通过才可交易判断。"
            "纳指 PE 来自 QQQ（stockanalysis/yfinance）**仅供参考**；样本不足时分位显示「无统计分位」。"
            "爬虫失败严禁用过期缓存做买卖。QDII溢价＞2%暂缓买入。"
            "短债012773不看PE/回撤。周四09:00定投周报；工作日监测档位/建仓事件邮件。",
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
