"""Send at most one daily action email for buy / take-profit signals.

Rules:
- A-share: buy if PE percentile < 40%; double if <= 30%; take-profit if >= 60%.
- US QDII: buy if PE percentile < 50%; take-profit if >= 70%;
  block buy when onshore ETF premium > 2%.
- No email when there is neither buy nor take-profit-with-holding signal,
  unless --force.
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

from policy_rules import classify_index, decision_label, load_policy, rules  # noqa: E402

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


def collect_signals(snapshot: dict, monthly: float, policy: dict) -> dict:
    indexes = snapshot.get("indexes", {})
    held = holdings_cost()
    r = rules(policy)
    a_buy = float(r.get("a_share_normal_percentile_below", 40))
    us_buy = float(r.get("us_normal_percentile_below", 50))

    rows: list[str] = []
    buy_a: list[str] = []
    buy_us: list[str] = []
    buy_lines: list[str] = []
    take_profit_lines: list[str] = []
    paused_amount = 0.0

    for name in (*A_SHARE, *US):
        item = indexes.get(name, {})
        pe = item.get("pe_ttm")
        pct = item.get("pe_percentile")
        premium = item.get("qdii_premium")
        action, reason = classify_index(
            name, pct, premium=premium, policy=policy
        )
        code, fund_name, weight = FUND_BY_INDEX[name]
        amount = round(monthly * weight, 2)
        pe_text = f"{pe:.2f}" if isinstance(pe, (int, float)) else "-"
        pct_text = f"{pct:.2f}%" if isinstance(pct, (int, float)) else "-"
        premium_text = (
            f"{item.get('qdii_premium_pct'):.2f}%"
            if isinstance(item.get("qdii_premium_pct"), (int, float))
            else "-"
        )
        label = decision_label(action)
        rows.append(
            f"- {name}｜PE {pe_text}｜分位 {pct_text}｜溢价 {premium_text}｜"
            f"{label}｜{reason}"
        )
        if action in ("buy", "double"):
            buy_lines.append(f"  · {fund_name}（{code}）约 {amount:.2f} 元（{label}）")
            if name in A_SHARE:
                buy_a.append(name)
            else:
                buy_us.append(name)
        elif action == "take_profit" and held.get(code, 0) > 0:
            cost = held[code]
            take_profit_lines.append(
                f"  · {fund_name}（{code}）建议赎回约 "
                f"{cost / 3:.2f}~{cost / 2:.2f} 元（当前账本 {cost:.2f}）"
            )
            paused_amount += amount
        else:
            paused_amount += amount

    short_code, short_name, short_w = SHORT_BOND
    short_base = round(monthly * short_w, 2)
    return {
        "rows": rows,
        "buy_a": buy_a,
        "buy_us": buy_us,
        "buy_lines": buy_lines,
        "take_profit_lines": take_profit_lines,
        "paused_amount": paused_amount,
        "short_code": short_code,
        "short_name": short_name,
        "short_base": short_base,
        "short_total": round(short_base + paused_amount, 2),
        "has_buy": bool(buy_a or buy_us),
        "has_take_profit": bool(take_profit_lines),
        "a_buy": a_buy,
        "us_buy": us_buy,
    }


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
            parts.append("买入")
        if data["has_take_profit"]:
            parts.append("止盈")
        why = "今日存在需要人工确认的行动：" + "、".join(parts) + "。"
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

    body = f"""{title}

生成时间：{now}
数据日期：{as_of}
月定投预算：{monthly:.0f} 元

【为何发这封邮件】
{why}
无行动信号时日常定时任务不发邮件。

【策略时点】
1）A股（沪深300 / 中证500）
   · 分位＜{a_buy:.0f}% → 可买；≤30% 加倍；≥60% 分批止盈
   · 下一个交易日 09:00~14:55 招行 APP 下单（15点前按当日净值）

2）美股 QDII（标普500 / 纳指100）
   · 分位＜{us_buy:.0f}% → 可买；≥70% 分批止盈
   · 场内 ETF 溢价＞2% 时暂缓买入；溢价回落至1%内再考虑
   · 当天交易日 15 点前下单

【今日结论】
A股可买：{a_text}
美股可买：{us_text}

【四指数估值】
{chr(10).join(data["rows"])}

【建议操作】
权益买入：
{buy_block}
分批止盈：
{tp_block}
短债底仓：{data["short_name"]}（{data["short_code"]}）约 {data["short_total"]:.2f} 元
  （固定短债 {data["short_base"]:.2f} + 权益暂停转入 {data["paused_amount"]:.2f}）

【执行提醒】
1. 以招行 APP 实际申购/赎回状态为准。
2. 买入后请运行：python scripts/record_holding.py buy --fund 代码 --amount 金额
3. 止盈后请运行：python scripts/record_holding.py sell --fund 代码 --amount 金额
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


def send_email(subject: str, body: str, dry_run: bool = False) -> None:
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
    print(f"已发送至 {masked}")


def main() -> None:
    parser = argparse.ArgumentParser(description="发送定投行动提醒（买入/止盈）")
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
    if not actionable and not args.force:
        print("跳过发送：无买入信号，也无持仓止盈信号。")
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
