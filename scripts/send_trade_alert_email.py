"""Send at most one daily buy-action email when PE thresholds are met.

Rules:
- A-share (沪深300 / 中证500): buy if PE percentile < 40%; order next session
  09:00~14:55 on CMB APP (same-day NAV if before 15:00).
- US QDII (标普500 / 纳指100): buy if PE percentile < 50%; order same day
  before 15:00 after morning PE refresh (~08:00~10:00).
- No email when nothing is below threshold.
- Scheduled send window: morning action mail only (once per trading day).

Email address and SMTP credentials come from environment / GitHub Secrets only.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "data" / "market_snapshot.json"
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
A_SHARE_BUY_BELOW = 40.0
US_BUY_BELOW = 50.0


def mask_email(address: str) -> str:
    """Mask local-part for logs / docs, e.g. 31****5734@qq.com."""
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


def signal_for(name: str, item: dict) -> tuple[str, str]:
    p = item.get("pe_percentile")
    if p is None:
        return "未知", "缺少 PE 分位，暂不建议买入"
    if name in A_SHARE:
        if p < A_SHARE_BUY_BELOW:
            return "可买", f"A股分位 {p:.2f}% < {A_SHARE_BUY_BELOW:.0f}%"
        return "暂停", f"A股分位 {p:.2f}% ≥ {A_SHARE_BUY_BELOW:.0f}%，不发买入提醒"
    if p < US_BUY_BELOW:
        return "可买", f"美股近10年分位 {p:.2f}% < {US_BUY_BELOW:.0f}%"
    return "暂停", f"美股近10年分位 {p:.2f}% ≥ {US_BUY_BELOW:.0f}%，不发买入提醒"


def collect_signals(snapshot: dict, monthly: float) -> dict:
    indexes = snapshot.get("indexes", {})
    rows: list[str] = []
    buy_a: list[str] = []
    buy_us: list[str] = []
    buy_lines: list[str] = []
    paused_amount = 0.0

    for name in (*A_SHARE, *US):
        item = indexes.get(name, {})
        pe = item.get("pe_ttm")
        pct = item.get("pe_percentile")
        signal, reason = signal_for(name, item)
        code, fund_name, weight = FUND_BY_INDEX[name]
        amount = round(monthly * weight, 2)
        pe_text = f"{pe:.2f}" if isinstance(pe, (int, float)) else "-"
        pct_text = f"{pct:.2f}%" if isinstance(pct, (int, float)) else "-"
        rows.append(f"- {name}｜PE {pe_text}｜分位 {pct_text}｜{signal}｜{reason}")
        if signal == "可买":
            buy_lines.append(f"  · {fund_name}（{code}）约 {amount:.2f} 元")
            if name in A_SHARE:
                buy_a.append(name)
            else:
                buy_us.append(name)
        else:
            paused_amount += amount

    short_code, short_name, short_w = SHORT_BOND
    short_base = round(monthly * short_w, 2)
    return {
        "rows": rows,
        "buy_a": buy_a,
        "buy_us": buy_us,
        "buy_lines": buy_lines,
        "paused_amount": paused_amount,
        "short_code": short_code,
        "short_name": short_name,
        "short_base": short_base,
        "short_total": round(short_base + paused_amount, 2),
        "has_buy": bool(buy_a or buy_us),
    }


def build_body(
    snapshot: dict, monthly: float, *, force: bool = False
) -> tuple[str, str]:
    as_of = snapshot.get("as_of", date.today().isoformat())
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")
    data = collect_signals(snapshot, monthly)

    if data["has_buy"]:
        title = f"【定投买入提醒】{as_of} 今日招行下单清单"
        why = (
            f"至少一只指数已低于策略买入分位"
            f"（A股＜{A_SHARE_BUY_BELOW:.0f}% 或 美股＜{US_BUY_BELOW:.0f}%）。"
        )
        names = "、".join(data["buy_a"] + data["buy_us"])
        subject = f"{title}｜可买：{names}"
    else:
        title = f"【定投联调】{as_of} 当前无买入信号"
        why = (
            "当前没有指数低于买入分位；本邮件仅因 --force / 手动联调而发送，"
            "日常定时任务不会发送。"
            if force
            else "当前没有指数低于买入分位。"
        )
        subject = title

    a_text = "、".join(data["buy_a"]) if data["buy_a"] else "无"
    us_text = "、".join(data["buy_us"]) if data["buy_us"] else "无"
    buy_block = "\n".join(data["buy_lines"]) if data["buy_lines"] else "  · 无"

    body = f"""{title}

生成时间：{now}
数据日期：{as_of}
月定投预算：{monthly:.0f} 元

【为何发这封邮件】
{why}
未达标的指数不会提醒买入；全部未达标时系统不发邮件。

【策略时点】
1）A股（沪深300 / 中证500）
   · 交易日傍晚 18:00~21:00 核对当日收盘 PE 分位
   · 分位＜{A_SHARE_BUY_BELOW:.0f}% → 可买
   · 下一个交易日 09:00~14:55 打开招行 APP 下单
   · 说明：15 点前下单按当天收盘净值成交，不必卡尾盘

2）美股 QDII（标普500 / 纳指100）
   · 美股凌晨收盘，早上 8~10 点更新前一晚最终 PE
   · 分位＜{US_BUY_BELOW:.0f}% → 可买
   · 当天交易日 15 点前，招行 APP 下单

【今日结论】
A股可买：{a_text}
美股可买：{us_text}
请在今天 15:00 前完成可买标的下单（A股建议 09:00~14:55）。

【四指数估值】
{chr(10).join(data["rows"])}

【建议操作】
权益买入：
{buy_block}
短债底仓：{data["short_name"]}（{data["short_code"]}）约 {data["short_total"]:.2f} 元
  （固定短债 {data["short_base"]:.2f} + 权益暂停转入 {data["paused_amount"]:.2f}）

【执行提醒】
1. 以招行 APP 实际申购状态为准；暂停申购则改短债。
2. 本邮件每天最多一封，且仅在有买入信号时发送。
3. 仅研究提醒，不构成投资建议，不会自动下单。

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
        # Common alternative: 587 + STARTTLS
        with smtplib.SMTP(cfg["host"], port, timeout=60) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["from"], [cfg["to"]], msg.as_string())
    print(f"已发送至 {masked}")


def main() -> None:
    parser = argparse.ArgumentParser(description="发送定投买入提醒（有信号才发，每天一次）")
    parser.add_argument(
        "--snapshot",
        default=str(SNAPSHOT_PATH),
        help="market_snapshot.json 路径",
    )
    parser.add_argument(
        "--monthly",
        type=float,
        default=env_float("MONTHLY_BUDGET", 300.0),
        help="每月定投金额，默认 300",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印，不真正发送",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使无买入信号也发送（仅用于联调）",
    )
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    if not snapshot_path.is_absolute():
        snapshot_path = ROOT / snapshot_path
    if not snapshot_path.is_file():
        raise SystemExit(f"找不到快照文件: {snapshot_path}")
    snapshot = load_snapshot(snapshot_path)

    data = collect_signals(snapshot, args.monthly)
    if not data["has_buy"] and not args.force:
        print(
            "跳过发送：没有指数低于买入分位"
            f"（A股＜{A_SHARE_BUY_BELOW:.0f}% / 美股＜{US_BUY_BELOW:.0f}%）。"
        )
        for row in data["rows"]:
            print(row)
        return

    if not data["has_buy"] and args.force:
        print("警告：无买入信号，但已指定 --force，仍将发送联调邮件。")

    subject, body = build_body(snapshot, args.monthly, force=args.force)
    send_email(subject, body, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
