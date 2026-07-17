"""Send at most one daily action email for buy / take-profit signals.

US rules (fail-closed):
- S&P 500: only when Multpl index PE is verified; else alert, never buy.
- Nasdaq 100: always unverified → never buy.
- Never use yfinance ETF PE for decisions.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from policy_rules import (  # noqa: E402
    bootstrap_planned_amount,
    bootstrap_summary_line,
    decision_label,
    load_policy,
    resolve_action,
    rules,
)
from trading_calendar import resolve_order_window, today_cst  # noqa: E402

SNAPSHOT_PATH = ROOT / "data" / "market_snapshot.json"
HOLDINGS_PATH = ROOT / "config" / "portfolio_holdings.json"
CST = timezone(timedelta(hours=8))

A_SHARE = ("沪深300", "中证500")
US = ("标普500", "纳斯达克100")
FUND_BY_INDEX = {
    "沪深300": ("460300", "华泰柏瑞沪深300ETF联接A", 0.27),
    "中证500": ("160119", "南方中证500ETF联接(LOF)A", 0.11),
    "标普500": ("050025", "博时标普500ETF联接A", 0.08),
    "纳斯达克100": ("016452", "南方纳斯达克100指数发起(QDII)A", 0.03),
}
SHORT_BOND = ("012773", "嘉实超短债债券A", 0.51)


def mask_email(address: str) -> str:
    if "@" not in address:
        return "***"
    local, domain = address.split("@", 1)
    if len(local) <= 4:
        masked = local[0] + "***"
    else:
        masked = local[:2] + "****" + local[-4:]
    return f"{masked}@{domain}"


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return float(raw)


def load_snapshot(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def holdings_cost() -> dict[str, float]:
    if not HOLDINGS_PATH.is_file():
        return {}
    doc = json.loads(HOLDINGS_PATH.read_text(encoding="utf-8"))
    return {
        item["fund_code"]: float(item.get("cost_basis") or 0)
        for item in doc.get("holdings", [])
    }


def building_principal() -> float:
    if not HOLDINGS_PATH.is_file():
        return 10000.0
    doc = json.loads(HOLDINGS_PATH.read_text(encoding="utf-8"))
    return float(doc.get("building_principal") or 10000.0)


def collect_signals(snapshot: dict, monthly: float, policy: dict) -> dict:
    indexes = snapshot.get("indexes", {})
    us_meta = snapshot.get("us_meta", {})
    held = holdings_cost()
    principal = building_principal()
    r = rules(policy)
    a_buy = float(r.get("a_share_normal_percentile_below", 40))
    us_buy = float(r.get("us_normal_percentile_below", 50))
    boot_line = bootstrap_summary_line(policy)

    rows: list[str] = []
    buy_a: list[str] = []
    buy_us: list[str] = []
    buy_lines: list[str] = []
    take_profit_lines: list[str] = []
    take_profit_a: list[str] = []
    take_profit_us: list[str] = []
    alert_lines: list[str] = list(us_meta.get("alerts") or [])
    paused_amount = 0.0
    spx_failed = False
    has_bootstrap = False

    for name in (*A_SHARE, *US):
        item = indexes.get(name, {})
        pe = item.get("pe_ttm")
        pct = item.get("pe_percentile")
        pct_1y = item.get("pe_percentile_1y")
        premium = item.get("qdii_premium")
        code, fund_name, weight = FUND_BY_INDEX[name]
        target_amount = principal * weight
        held_cost = float(held.get(code, 0) or 0)
        action, reason = resolve_action(
            name,
            pct,
            percentile_1y=pct_1y,
            drawdown_from_52w_high=item.get("drawdown_from_52w_high"),
            premium=premium,
            policy=policy,
            verified=item.get("verified"),
            tradeable=item.get("tradeable"),
            held_cost=held_cost,
            target_amount=target_amount,
        )
        month_slice = round(monthly * weight, 2)
        if action == "bootstrap":
            amount = bootstrap_planned_amount(held_cost, target_amount, policy)
            has_bootstrap = True
        elif action == "double":
            amount = round(month_slice * 2, 2)
        elif action == "half":
            frac_key = (
                "a_share_half_fraction" if name in A_SHARE else "us_half_fraction"
            )
            frac = float(r.get(frac_key, 0.5))
            amount = round(month_slice * frac, 2)
        else:
            amount = month_slice
        pe_text = f"{pe:.2f}" if isinstance(pe, (int, float)) else "-"
        pct_text = f"{pct:.2f}%" if isinstance(pct, (int, float)) else (
            "无统计分位" if name == "纳斯达克100" else "-"
        )
        pct_1y_text = f"{pct_1y:.2f}%" if isinstance(pct_1y, (int, float)) else (
            "无统计分位" if name == "纳斯达克100" else "-"
        )
        dd = item.get("drawdown_from_52w_high_pct")
        dd_text = f"{dd:.2f}%" if isinstance(dd, (int, float)) else "-"
        premium_text = (
            f"{item.get('qdii_premium_pct'):.2f}%"
            if isinstance(item.get("qdii_premium_pct"), (int, float))
            else "-"
        )
        label = decision_label(action)
        verified_flag = item.get("verified")
        if name in A_SHARE:
            verify_text = "A股源"
        elif name == "纳斯达克100":
            verify_text = "仅参考"
        elif verified_flag is True:
            verify_text = "已核验"
        else:
            verify_text = "未核验"
        rows.append(
            f"- {name}｜PE {pe_text}｜10年分位 {pct_text}｜1年分位 {pct_1y_text}｜"
            f"52周回撤 {dd_text}｜溢价 {premium_text}｜{verify_text}｜{label}｜{reason}"
        )
        if name == "标普500" and verified_flag is not True:
            spx_failed = True
            for err in item.get("validation_errors") or [reason]:
                if err not in alert_lines:
                    alert_lines.append(str(err))

        if action in ("buy", "double", "half", "bootstrap"):
            buy_lines.append(f"  · {fund_name}（{code}）约 {amount:.2f} 元（{label}）")
            if name in A_SHARE:
                buy_a.append(name)
            else:
                buy_us.append(name)
            if action in ("bootstrap", "half"):
                paused_amount += max(month_slice - amount, 0.0)
        elif action == "take_profit" and held_cost > 0:
            take_profit_lines.append(
                f"  · {fund_name}（{code}）建议赎回约 "
                f"{held_cost / 3:.2f}~{held_cost / 2:.2f} 元（当前账本 {held_cost:.2f}）"
            )
            if name in A_SHARE:
                take_profit_a.append(name)
            else:
                take_profit_us.append(name)
            paused_amount += month_slice
        else:
            paused_amount += month_slice

    short_code, short_name, short_w = SHORT_BOND
    short_base = round(monthly * short_w, 2)
    return {
        "rows": rows,
        "buy_a": buy_a,
        "buy_us": buy_us,
        "buy_lines": buy_lines,
        "take_profit_lines": take_profit_lines,
        "alert_lines": alert_lines,
        "spx_failed": spx_failed,
        "paused_amount": paused_amount,
        "short_code": short_code,
        "short_name": short_name,
        "short_base": short_base,
        "short_total": round(short_base + paused_amount, 2),
        "has_buy": bool(buy_a or buy_us),
        "has_bootstrap": has_bootstrap,
        "has_take_profit": bool(take_profit_lines),
        "take_profit_a": take_profit_a,
        "take_profit_us": take_profit_us,
        "has_a_action": bool(buy_a or take_profit_a),
        "has_us_action": bool(buy_us or take_profit_us),
        "has_us_alert": spx_failed or bool(alert_lines),
        "a_buy": a_buy,
        "us_buy": us_buy,
        "boot_line": boot_line,
        "principal": principal,
    }


def build_body(
    snapshot: dict,
    monthly: float,
    policy: dict,
    *,
    force: bool = False,
    slot: str = "morning",
    timing: dict[str, str] | None = None,
) -> tuple[str, str]:
    timing = timing or resolve_order_window(
        slot, as_of=snapshot.get("as_of"), today=today_cst()
    )
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")
    data = collect_signals(snapshot, monthly, policy)
    slot_norm = timing["slot"]

    if slot_norm == "evening":
        window_label = "晚间A股收盘后信号"
        order_hint = timing["instruction"]
    else:
        window_label = "上午操作提醒（含QDII）"
        order_hint = timing["instruction"]

    # Evening focuses on A-share; US/QDII wait for morning premium/QDII refresh.
    evening_focus = slot_norm == "evening"
    buy_names = data["buy_a"] if evening_focus else (data["buy_a"] + data["buy_us"])
    has_action_for_slot = (
        data["has_a_action"]
        if evening_focus
        else (data["has_buy"] or data["has_take_profit"])
    )

    if has_action_for_slot or (
        force and (data["has_buy"] or data["has_take_profit"])
    ):
        title = f"【定投行动提醒】{timing['signal_date']}｜{window_label}"
        parts = []
        if evening_focus:
            if data["buy_a"]:
                parts.append(
                    "A股微建仓/买入/半额" if data.get("has_bootstrap") else "A股买入/半额"
                )
            if data.get("take_profit_a"):
                parts.append("A股止盈")
        else:
            if data["has_buy"]:
                parts.append(
                    "微建仓/买入/半额" if data.get("has_bootstrap") else "买入/半额"
                )
            if data["has_take_profit"]:
                parts.append("止盈")
        why = "存在需要人工确认的行动：" + ("、".join(parts) if parts else "联调") + "。"
        if data["spx_failed"]:
            why += " 注意：标普估值校验失败，美股买入信号已禁止。"
        if evening_focus and data.get("has_us_action"):
            why += " 美股/QDII 信号请等上午邮件（溢价复核后再操作）。"
        why += f" {order_hint}"
        names = "、".join(buy_names)
        subject = (
            f"{title}｜可买：{names}" if names else f"{title}｜止盈观察"
        )
    else:
        title = f"【定投联调】{timing['signal_date']}｜{window_label}｜无行动信号"
        why = (
            "当前无买入/止盈行动；本邮件仅因 --force / 手动联调而发送。"
            if force
            else "当前无买入/止盈行动。"
        )
        subject = title

    a_text = "、".join(data["buy_a"]) if data["buy_a"] else "无"
    us_text = "、".join(data["buy_us"]) if data["buy_us"] else "无"

    # Filter suggested orders: evening = A-share funds only.
    a_codes = {FUND_BY_INDEX[n][0] for n in A_SHARE}

    def _is_a_line(line: str) -> bool:
        return any(code in line for code in a_codes)

    if evening_focus:
        buy_block_lines = [line for line in data["buy_lines"] if _is_a_line(line)]
        tp_block_lines = [
            line for line in data["take_profit_lines"] if _is_a_line(line)
        ]
        us_preview_lines = [
            f"  · （仅预览，勿今晚下单）{line.lstrip(' ·')}"
            for line in data["buy_lines"]
            if not _is_a_line(line)
        ]
    else:
        buy_block_lines = list(data["buy_lines"])
        tp_block_lines = list(data["take_profit_lines"])
        us_preview_lines = []

    buy_block = (
        "\n".join(buy_block_lines + us_preview_lines)
        if (buy_block_lines or us_preview_lines)
        else "  · 无"
    )
    tp_block = (
        "\n".join(tp_block_lines)
        if tp_block_lines
        else "  · 无（账本无对应持仓时仅观察）"
    )
    alert_block = (
        "\n".join(f"- {line}" for line in data["alert_lines"])
        if data["alert_lines"]
        else "- 无"
    )

    focus_note = (
        "本封为【晚间】邮件：以 A 股收盘后估值为主；请在下一个交易日 15:00 前操作。"
        "不把今晚判断写成「今晚已按今日净值成交」。"
        if slot_norm == "evening"
        else "本封为【上午】邮件：可复核 QDII/溢价，并提醒今日 15:00 前执行；"
        "此处不是「今日盘中新收盘判断」。"
    )

    body = f"""{title}

生成时间：{now}
邮件时段：{slot_norm}（{window_label}）
signal_date（估值信号日）：{timing["signal_date"]}
order_date（建议申购日）：{timing["order_date"]}
cutoff_time：{timing["cutoff_time"]}
{order_hint}

【为何发这封邮件】
{why}
{focus_note}

【策略时点】
1）A股近10年分位：＜30%加倍；30%~40%满额；40%~60%半额；≥60%停买/止盈观察
2）标普500（Multpl核验通过后）：＜{data["us_buy"]:.0f}%满额；50%~70%半额；≥70%停买/止盈观察
3）微建仓例外：{data.get('boot_line', '')}（纳指除外）
4）纳斯达克100：估值未核验，永不自动买入
5）QDII 场内溢价＞2% 暂缓买入

【信号结论】
A股可买/半额：{a_text}
美股可买/半额：{us_text}

【美股核验告警】
{alert_block}

【四指数估值】
{chr(10).join(data["rows"])}

【建议操作】
权益买入：
{buy_block}
分批止盈：
{tp_block}
短债底仓：{data["short_name"]}（{data["short_code"]}）约 {data["short_total"]:.2f} 元

【场外申购说明（招行/工行等）】
- {timing["nav_note_a_share"]}
- {timing["nav_note_qdii"]}
- 估值信号日 ≠ 申购成交净值日：这是场外未知价机制下的正常现象。

【执行提醒】
1. 以银行 APP 实际申购/赎回状态与基金合同为准。
2. 买入：python scripts/record_holding.py buy --fund 代码 --amount 金额 [--nav 净值]
3. 卖出：python scripts/record_holding.py sell --fund 代码 --proceeds 市值 --cost 成本
4. 仅研究提醒，不构成投资建议，不会自动下单。

— My Fund AI Assistant
"""
    return subject, body


def has_trade_action(data: dict) -> bool:
    """True only when user needs to buy/sell in the bank app."""
    return bool(data.get("has_a_action") or data.get("has_us_action"))


def should_send_for_slot(data: dict, slot: str, *, force: bool) -> tuple[bool, str]:
    """Send only when there is a real trade action; never for valuation-only noise."""
    if not has_trade_action(data):
        return False, "no_trade_action"
    slot_norm = (slot or "morning").strip().lower()
    if force:
        return True, "force_with_action"
    if slot_norm == "evening":
        if data.get("has_a_action"):
            return True, "a_share_action"
        return False, "evening_no_a_share_action"
    # morning: A-share reminder and/or US/QDII action
    return True, "morning_action"


def require_mail_config() -> dict[str, str]:
    to_addr = os.environ.get("ALERT_EMAIL", "").strip()
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_pass = os.environ.get("SMTP_PASS", "").strip()
    smtp_host = os.environ.get("SMTP_HOST", "").strip() or "smtp.qq.com"
    smtp_port = os.environ.get("SMTP_PORT", "").strip() or "465"
    mail_from = os.environ.get("MAIL_FROM", "").strip() or smtp_user

    missing = [
        name
        for name, value in (
            ("ALERT_EMAIL", to_addr),
            ("SMTP_USER", smtp_user),
            ("SMTP_PASS", smtp_pass),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            "缺少邮件环境变量: "
            + ", ".join(missing)
            + "。请在本地 .env 或 GitHub Secrets 配置，勿把完整邮箱写入仓库。"
        )
    return {
        "to": to_addr,
        "user": smtp_user,
        "password": smtp_pass,
        "host": smtp_host,
        "port": smtp_port,
        "from": mail_from,
    }


def _write_github_summary(markdown: str) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary:
        return
    with open(summary, "a", encoding="utf-8") as handle:
        handle.write(markdown)
        if not markdown.endswith("\n"):
            handle.write("\n")


def send_email(
    subject: str,
    body: str,
    dry_run: bool = False,
    *,
    retries: int = 2,
) -> None:
    if dry_run:
        to_addr = os.environ.get("ALERT_EMAIL", "").strip() or "unset@example.com"
        print(f"准备发送至 {mask_email(to_addr)} | subject={subject}")
        print("--- dry-run body ---")
        print(body)
        return

    cfg = require_mail_config()
    masked = mask_email(cfg["to"])
    print(f"准备发送至 {masked} | subject={subject}")

    msg = MIMEMultipart()
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    context = ssl.create_default_context()
    port = int(cfg["port"])
    last_error: Exception | None = None
    attempts = max(1, retries + 1)
    for attempt in range(1, attempts + 1):
        try:
            if port == 465:
                with smtplib.SMTP_SSL(cfg["host"], port, context=context) as server:
                    server.login(cfg["user"], cfg["password"])
                    server.sendmail(cfg["from"], [cfg["to"]], msg.as_string())
            else:
                with smtplib.SMTP(cfg["host"], port, timeout=60) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(cfg["user"], cfg["password"])
                    server.sendmail(cfg["from"], [cfg["to"]], msg.as_string())
            print(f"已发送至 {masked}（第 {attempt} 次尝试）")
            _write_github_summary(
                f"### 邮件发送成功\n\n- 收件人: `{masked}`\n- 主题: {subject}\n"
            )
            return
        except Exception as exc:
            last_error = exc
            print(f"邮件发送失败（第 {attempt}/{attempts} 次）: {exc}", file=sys.stderr)
            if attempt < attempts:
                import time

                time.sleep(2 * attempt)

    _write_github_summary(
        "### 邮件发送失败\n\n"
        f"- 收件人: `{masked}`\n"
        f"- 主题: {subject}\n"
        f"- 错误: `{last_error}`\n"
        "- 工作流将标记为失败，请检查 SMTP Secrets。\n"
    )
    raise SystemExit(f"邮件发送失败（已重试 {attempts} 次）: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="仅在有买入/止盈等实际操作时发送提醒；区分晚间/上午申购窗口语义"
    )
    parser.add_argument("--snapshot", default=str(SNAPSHOT_PATH))
    parser.add_argument(
        "--monthly",
        type=float,
        default=env_float("MONTHLY_BUDGET", 300.0),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="有操作时忽略时段过滤（晚间也可发美股）；无操作仍不发信",
    )
    parser.add_argument(
        "--slot",
        choices=("morning", "evening"),
        default="morning",
        help="morning=今日15:00前操作提醒；evening=下一交易日15:00前操作提醒",
    )
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    if not snapshot_path.is_absolute():
        snapshot_path = ROOT / snapshot_path
    if not snapshot_path.is_file():
        raise SystemExit(f"找不到快照文件: {snapshot_path}")
    snapshot = load_snapshot(snapshot_path)
    policy = load_policy()
    timing = resolve_order_window(
        args.slot, as_of=snapshot.get("as_of"), today=today_cst()
    )
    print(
        f"邮件时段={timing['slot']} signal_date={timing['signal_date']} "
        f"order_date={timing['order_date']} cutoff={timing['cutoff_time']}"
    )

    data = collect_signals(snapshot, args.monthly, policy)

    # SPX failure: fail-closed on buys already; do NOT email without a trade action.
    if data["spx_failed"] and not has_trade_action(data):
        msg = (
            "跳过发送：美股估值未核验且无买入/止盈操作"
            "（告警写入日志/Summary，不发空操作邮件）。"
        )
        print(msg)
        for row in data["rows"]:
            print(row)
        for line in data["alert_lines"]:
            print(f"告警: {line}")
        _write_github_summary(
            "### 邮件跳过（无操作）\n\n"
            "- 原因: 美股核验失败或仅有告警，无银行 APP 买入/止盈动作\n"
            "- 策略: **无操作不发信**\n"
        )
        return

    should, reason = should_send_for_slot(data, args.slot, force=args.force)
    if not should:
        print(f"跳过发送：slot={args.slot} reason={reason}")
        for row in data["rows"]:
            print(row)
        _write_github_summary(
            "### 邮件跳过（无操作）\n\n"
            f"- slot: `{args.slot}`\n"
            f"- reason: `{reason}`\n"
        )
        return

    subject, body = build_body(
        snapshot,
        args.monthly,
        policy,
        force=args.force,
        slot=args.slot,
        timing=timing,
    )
    send_email(subject, body, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
