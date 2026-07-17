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
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from policy_rules import (  # noqa: E402
    bootstrap_remaining,
    decision_label,
    load_policy,
    resolve_action,
    rules,
)

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
    boot_cfg = policy.get("bootstrap") or {}
    boot_line = (
        f"近1年分位≤{float(boot_cfg.get('percentile_at_or_below', 30)):.0f}% "
        f"且未满目标仓{float(boot_cfg.get('max_fraction_of_target', 0.15)) * 100:.0f}% "
        f"时可建启动仓"
        if boot_cfg.get("enabled")
        else "启动仓未启用"
    )

    rows: list[str] = []
    buy_a: list[str] = []
    buy_us: list[str] = []
    buy_lines: list[str] = []
    take_profit_lines: list[str] = []
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
            premium=premium,
            policy=policy,
            verified=item.get("verified"),
            tradeable=item.get("tradeable"),
            held_cost=held_cost,
            target_amount=target_amount,
        )
        month_slice = round(monthly * weight, 2)
        # Align with build_plan: starter uses first-month sleeve of building principal.
        first_month_slice = round(principal * 0.20 * weight, 2)
        if action == "bootstrap":
            amount = round(
                min(
                    first_month_slice,
                    bootstrap_remaining(held_cost, target_amount, policy),
                ),
                2,
            )
            has_bootstrap = True
        elif action == "double":
            amount = round(month_slice * 2, 2)
        else:
            amount = month_slice
        pe_text = f"{pe:.2f}" if isinstance(pe, (int, float)) else "-"
        pct_text = f"{pct:.2f}%" if isinstance(pct, (int, float)) else (
            "无统计分位" if name == "纳斯达克100" else "-"
        )
        pct_1y_text = f"{pct_1y:.2f}%" if isinstance(pct_1y, (int, float)) else (
            "无统计分位" if name == "纳斯达克100" else "-"
        )        premium_text = (
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
            f"溢价 {premium_text}｜{verify_text}｜{label}｜{reason}"
        )
        if name == "标普500" and verified_flag is not True:
            spx_failed = True
            for err in item.get("validation_errors") or [reason]:
                if err not in alert_lines:
                    alert_lines.append(str(err))

        if action in ("buy", "double", "bootstrap"):
            buy_lines.append(f"  · {fund_name}（{code}）约 {amount:.2f} 元（{label}）")
            if name in A_SHARE:
                buy_a.append(name)
            else:
                buy_us.append(name)
            if action == "bootstrap":
                # Remainder of the monthly equity sleeve stays in short bond.
                paused_amount += max(month_slice - amount, 0.0)
        elif action == "take_profit" and held_cost > 0:
            take_profit_lines.append(
                f"  · {fund_name}（{code}）建议赎回约 "
                f"{held_cost / 3:.2f}~{held_cost / 2:.2f} 元（当前账本 {held_cost:.2f}）"
            )
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
        "has_us_alert": spx_failed or bool(alert_lines),
        "a_buy": a_buy,
        "us_buy": us_buy,
        "boot_line": boot_line,
        "principal": principal,
    }


def build_alert_body(snapshot: dict, data: dict) -> tuple[str, str]:
    as_of = snapshot.get("as_of", date.today().isoformat())
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")
    alerts = "\n".join(f"- {line}" for line in data["alert_lines"]) or "- （无明细）"
    subject = f"【估值告警】{as_of} 美股估值未核验，请勿按邮件操作买入"
    body = f"""【估值获取失败 / 未核验】

生成时间：{now}
数据日期：{as_of}

系统未能完成美股指数估值核验（或纳指仍为未核验状态）。
按策略硬规则：禁止输出买入提醒，请勿依据本邮件下单。

【告警明细】
{alerts}

【四指数快照】
{chr(10).join(data["rows"])}

【执行提醒】
1. 标普500 需 Multpl 指数 PE + 近10年分位校验通过后才可自动判断。
2. 纳斯达克100 现阶段永久未核验，永不自动生成买入信号。
3. 严禁使用过期缓存估值继续买卖判断。

— My Fund AI Assistant
"""
    return subject, body


def build_body(
    snapshot: dict, monthly: float, policy: dict, *, force: bool = False
) -> tuple[str, str]:
    as_of = snapshot.get("as_of", date.today().isoformat())
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")
    data = collect_signals(snapshot, monthly, policy)
    a_buy = data["a_buy"]
    us_buy = data["us_buy"]

    if data["has_buy"] or data["has_take_profit"]:
        title = f"【定投行动提醒】{as_of} 今日招行操作清单"
        parts = []
        if data["has_buy"]:
            parts.append("启动仓/买入" if data.get("has_bootstrap") else "买入")
        if data["has_take_profit"]:
            parts.append("止盈")
        why = "今日存在需要人工确认的行动：" + "、".join(parts) + "。"
        if data["spx_failed"]:
            why += " 注意：标普估值校验失败，美股买入信号已禁止。"
        names = "、".join(data["buy_a"] + data["buy_us"])
        subject = (
            f"{title}｜可买：{names}" if names else f"{title}｜止盈观察"
        )
    else:
        title = f"【定投联调】{as_of} 当前无行动信号"
        why = (
            "当前无买入/止盈行动；本邮件仅因 --force / 手动联调而发送。"
            if force
            else "当前无买入/止盈行动。"
        )
        subject = title

    a_text = "、".join(data["buy_a"]) if data["buy_a"] else "无"
    us_text = "、".join(data["buy_us"]) if data["buy_us"] else "无"
    buy_block = "\n".join(data["buy_lines"]) if data["buy_lines"] else "  · 无"
    tp_block = (
        "\n".join(data["take_profit_lines"])
        if data["take_profit_lines"]
        else "  · 无（账本无对应持仓时仅观察）"
    )
    alert_block = (
        "\n".join(f"- {line}" for line in data["alert_lines"])
        if data["alert_lines"]
        else "- 无"
    )

    body = f"""{title}

生成时间：{now}
数据日期：{as_of}
建仓本金：{data.get('principal', 10000):.0f} 元
月定投预算：{monthly:.0f} 元

【为何发这封邮件】
{why}

【策略时点】
1）A股近10年分位＜{a_buy:.0f}% 可买；≤30% 加倍；≥60% 分批止盈
2）标普500：Multpl核验通过后，近10年分位＜{us_buy:.0f}% 可买；≥70% 止盈
3）启动仓例外：{data.get('boot_line', '')}（纳指除外）
4）纳斯达克100：估值未核验，永不自动买入
5）QDII 场内溢价＞2% 暂缓买入

【今日结论】
A股可买：{a_text}
美股可买：{us_text}

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

【执行提醒】
1. 以招行 APP 实际申购/赎回状态为准。
2. 买入：python scripts/record_holding.py buy --fund 代码 --amount 金额 [--nav 净值]
3. 卖出：python scripts/record_holding.py sell --fund 代码 --proceeds 市值 --cost 成本
   或 --proceeds 市值 --shares 份额（按持仓比例扣成本）
4. 仅研究提醒，不构成投资建议，不会自动下单。

— My Fund AI Assistant
"""
    return subject, body


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
    parser = argparse.ArgumentParser(description="发送定投行动提醒（买入/止盈/估值告警）")
    parser.add_argument("--snapshot", default=str(SNAPSHOT_PATH))
    parser.add_argument(
        "--monthly",
        type=float,
        default=env_float("MONTHLY_BUDGET", 300.0),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    if not snapshot_path.is_absolute():
        snapshot_path = ROOT / snapshot_path
    if not snapshot_path.is_file():
        raise SystemExit(f"找不到快照文件: {snapshot_path}")
    snapshot = load_snapshot(snapshot_path)
    policy = load_policy()

    data = collect_signals(snapshot, args.monthly, policy)
    actionable = data["has_buy"] or data["has_take_profit"]

    # SPX validation failure → alert only (no US buy already enforced).
    if data["spx_failed"] and not actionable and not args.force:
        subject, body = build_alert_body(snapshot, data)
        send_email(subject, body, dry_run=args.dry_run)
        return

    if not actionable and not args.force:
        print("跳过发送：无买入/止盈信号，且标普估值校验通过（或仅有纳指未核验提示）。")
        for row in data["rows"]:
            print(row)
        return

    if not actionable and args.force:
        print("警告：无行动信号，但已指定 --force，仍将发送联调邮件。")

    subject, body = build_body(
        snapshot, args.monthly, policy, force=args.force
    )
    send_email(subject, body, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
