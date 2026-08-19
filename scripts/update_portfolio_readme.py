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

from cost_basis_metrics import (  # noqa: E402
    format_index_level,
    holding_cost_metrics,
    implied_avg_index_level,
    vs_avg_pct,
)
from fund_nav import EQUITY_COST_FUNDS  # noqa: E402
from index_spot import attach_a_share_spots  # noqa: E402
from investment_plan import (  # noqa: E402
    build_summary_line,
    dca_summary_line,
)
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

FUND_TO_INDEX = {
    "460300": "沪深300",
    "160119": "中证500",
    "050025": "标普500",
    "016452": "纳斯达克100",
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
        if name == "纳斯达克100" and action == "reference":
            label = "仅参考·个人定投另计"
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
    # Prefer live App-style points (000300 ≈ 4582.68) over yesterday's daily close.
    try:
        attach_a_share_spots(indexes)
    except Exception as exc:  # noqa: BLE001
        print(f"警告: 实时指数点位刷新失败（{exc}）", file=sys.stderr)

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

        metrics = holding_cost_metrics(item, fund.get("nav"))
        index_name = FUND_TO_INDEX.get(str(item.get("fund_code") or ""))
        index_meta = indexes.get(index_name or "", {}) if index_name else {}
        # Live quote for display; prev/daily close anchors buy-average estimate.
        index_spot = index_meta.get("price_spot")
        index_anchor = (
            index_meta.get("price_prev_close")
            or index_meta.get("price_close")
            or index_spot
        )
        index_display = index_spot if isinstance(index_spot, (int, float)) else index_anchor
        avg_index = implied_avg_index_level(
            index_anchor, metrics.get("avg_cost"), metrics.get("nav")
        )
        vs_index = vs_avg_pct(
            float(index_display) if isinstance(index_display, (int, float)) else None,
            avg_index,
        )
        if (
            item.get("fund_code") in EQUITY_COST_FUNDS
            and metrics.get("vs_avg_pct") is not None
        ):
            sign = "+" if metrics["vs_avg_pct"] >= 0 else ""
            reason = (
                f"{reason}；相对持仓均价 {sign}{metrics['vs_avg_pct']:.2f}%"
                f"（均价 {metrics['avg_cost']:.4f} / 净值 {float(fund.get('nav')):.4f}）"
            )
            if isinstance(index_display, (int, float)) and avg_index is not None:
                reason += (
                    f"；指数点位 {format_index_level(index_display)}"
                    f" / 买入均点 {format_index_level(avg_index)}"
                )

        rows.append(
            {
                "fund_code": item["fund_code"],
                "name": item["name"],
                "cost_basis": cost,
                "shares": metrics.get("shares"),
                "avg_cost": metrics.get("avg_cost"),
                "vs_avg_pct": metrics.get("vs_avg_pct"),
                "index_name": index_name,
                "index_close": float(index_display)
                if isinstance(index_display, (int, float))
                else None,
                "index_spot": float(index_spot)
                if isinstance(index_spot, (int, float))
                else None,
                "avg_index_level": avg_index,
                "vs_index_pct": vs_index,
                "index_price_date": index_meta.get("price_spot_as_of")
                or index_meta.get("date"),
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


def short_fund_label(name: str, fund_code: str) -> str:
    """Compact fund label for narrow README tables (phone-friendly)."""
    aliases = {
        "012773": "短债",
        "160119": "中证500",
        "016452": "纳指100",
        "460300": "沪深300",
        "050025": "标普500",
    }
    label = aliases.get(str(fund_code)) or str(name or fund_code)
    return f"{label} `{fund_code}`"


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
        "> 投入占比 = 已投入 ÷ 1万元本金；相对均价 =（净值 − 均价）/ 均价；"
        "权益相对均价 ≥8% 时组合定投/建仓建议软降级。"
        "指数点位 = 券商 App 同口径实时报价（如沪深300 4582.68）；"
        "买入均点 ≈ 昨收 ×（持仓均价 ÷ 净值）；相对均点按实时点位对比买入均点。",
        "",
        "#### 建仓进度",
        "",
        "| 基金 | 已投入 | 目标 | 还差 | 状态 |",
        "|---|---:|---:|---:|---|",
    ]
    for row in status["rows"]:
        lines.append(
            f"| {short_fund_label(row['name'], row['fund_code'])} | "
            f"{money(row['cost_basis'])} | {row['target_percent']:.0f}% / "
            f"{money(row['target_amount'])} | {money(row['shortfall'])} | "
            f"{row['decision']} |"
        )

    equity_rows = [
        row
        for row in status["rows"]
        if row.get("fund_code") in EQUITY_COST_FUNDS
        and (
            isinstance(row.get("avg_cost"), (int, float))
            or isinstance(row.get("nav"), (int, float))
        )
    ]
    if equity_rows:
        lines.extend(
            [
                "",
                "#### 权益均价（相对自己的成本）",
                "",
                "| 基金 | 份额 | 均价 | 净值 | 相对均价 |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for row in equity_rows:
            shares = row.get("shares")
            avg = row.get("avg_cost")
            nav = row.get("nav")
            vs = row.get("vs_avg_pct")
            shares_text = f"{shares:.2f}" if isinstance(shares, (int, float)) else "-"
            avg_text = f"{avg:.4f}" if isinstance(avg, (int, float)) else "-"
            nav_text = f"{float(nav):.4f}" if isinstance(nav, (int, float)) else "-"
            vs_text = f"**{vs:+.2f}%**" if isinstance(vs, (int, float)) else "-"
            lines.append(
                f"| {short_fund_label(row['name'], row['fund_code'])} | "
                f"{shares_text} | {avg_text} | {nav_text} | {vs_text} |"
            )

    index_level_rows = [
        row
        for row in status["rows"]
        if row.get("fund_code") in EQUITY_COST_FUNDS
        and (
            isinstance(row.get("index_close"), (int, float))
            or isinstance(row.get("avg_index_level"), (int, float))
        )
    ]
    if index_level_rows:
        lines.extend(
            [
                "",
                "#### 指数点位（更直观）",
                "",
                "| 指数 | 今日点位 | 买入均点 | 相对均点 |",
                "|---|---:|---:|---:|",
            ]
        )
        for row in index_level_rows:
            index_name = row.get("index_name") or short_fund_label(
                row["name"], row["fund_code"]
            )
            close = row.get("index_close")
            avg_lvl = row.get("avg_index_level")
            vs = row.get("vs_index_pct")
            if vs is None:
                vs = row.get("vs_avg_pct")
            date_bit = row.get("index_price_date")
            name_text = str(index_name)
            if date_bit:
                name_text = f"{name_text}（{date_bit}）"
            lines.append(
                f"| {name_text} | {format_index_level(close)} | "
                f"{format_index_level(avg_lvl)} | "
                f"{f'**{vs:+.2f}%**' if isinstance(vs, (int, float)) else '-'} |"
            )

    equity_notes = status.get("equity_notes") or []
    if equity_notes:
        lines.extend(["", "### 权益信号速览", ""])
        for note in equity_notes:
            lines.append(f"- {note}")
    lines.extend(["", "### 今日判断依据", ""])
    for row in status["rows"]:
        lines.append(
            f"- {short_fund_label(row['name'], row['fund_code'])}：{row['reason']}。"
        )
    lines.extend(
        [
            "",
            "## 今日权益估值（4支）",
            "",
            "> PE 看贵不贵；点位看高低；场外按确认净值成交。",
            "",
            "| 标的 | 场外 | 点位 | PE | 10年 | 1年 | 回撤 | 溢价 | 判断 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---|",
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
    for name, _market_code, fund_code in index_rows:
        index = status["indexes"].get(name, {})
        pe = index.get("pe_ttm")
        percentile = index.get("pe_percentile")
        percentile_1y = index.get("pe_percentile_1y")
        premium_pct = index.get("qdii_premium_pct")
        dd_pct = index.get("drawdown_from_52w_high_pct")
        price_close = index.get("price_spot") or index.get("price_close")
        if name in ("沪深300", "中证500"):
            premium_text = "-"
        elif isinstance(premium_pct, (int, float)):
            premium_text = f"{premium_pct:.2f}%"
        else:
            premium_text = "待核验"
        dd_text = f"{dd_pct:.1f}%" if isinstance(dd_pct, (int, float)) else "-"
        level_text = format_index_level(
            float(price_close) if isinstance(price_close, (int, float)) else None
        )
        code, weight = INDEX_WEIGHT[name]
        held = float(holdings_cost.get(code, 0) or 0)
        action, _reason = resolve_action(
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
        if isinstance(percentile, (int, float)):
            p10 = f"{percentile:.1f}%"
        elif name == "纳斯达克100":
            p10 = "无统计"
        else:
            p10 = "-"
        if isinstance(percentile_1y, (int, float)):
            p1 = f"{percentile_1y:.1f}%"
        elif name == "纳斯达克100":
            p1 = "无统计"
        else:
            p1 = "-"
        short_name = {
            "沪深300": "沪深300",
            "中证500": "中证500",
            "标普500": "标普500",
            "纳斯达克100": "纳指100",
        }.get(name, name)
        decision = decision_label(action)
        if action == "reference" or name == "纳斯达克100":
            decision = "仅参考·个人定投另计"
        elif action == "overvalued_watch":
            decision = "高估观察"
        elif action == "unknown":
            decision = "未核验" if index.get("verified") is not True else "数据不足"
        lines.append(
            f"| {short_name} | `{fund_code}` | {level_text} | {pe_text} | {p10} | "
            f"{p1} | {dd_text} | {premium_text} | {decision} |"
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
            "短债012773不看PE/回撤。周四约12:00定投周报；工作日监测档位/建仓事件邮件。",
            "",
            f"> 数据状态：{status['data_status']}。AI 只提供研究建议，不自动下单。",
            END,
        ]
    )
    return "\n".join(lines) + "\n"


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
