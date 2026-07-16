"""Send timed trade reminders based on portfolio valuation rules.

Email address and SMTP credentials must come from environment / GitHub Secrets.
This script never hardcodes a personal mailbox.
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


def load_snapshot(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def signal_for(name: str, item: dict) -> tuple[str, str]:
    p = item.get("pe_percentile")
    if p is None:
        return "未知", "缺少 PE 分位，暂不建议买入"
    if name in A_SHARE:
        if p < 40:
            return "可买", f"A股分位 {p:.2f}% < 40%"
        return "暂停", f"A股分位 {p:.2f}% ≥ 40%，资金转短债"
    if p < 50:
        return "可买", f"美股近10年分位 {p:.2f}% < 50%"
    return "暂停", f"美股近10年分位 {p:.2f}% ≥ 50%，资金转短债"


def next_trading_day_hint(today: date) -> str:
    # Simple weekday roll-forward; ignores A-share holidays.
    d = today + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def build_body(mode: str, snapshot: dict, monthly: float) -> tuple[str, str]:
    as_of = snapshot.get("as_of", date.today().isoformat())
    indexes = snapshot.get("indexes", {})
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")

    rows = []
    buy_a: list[str] = []
    buy_us: list[str] = []
    paused_amount = 0.0
    buy_lines: list[str] = []

    for name in (*A_SHARE, *US):
        item = indexes.get(name, {})
        pe = item.get("pe_ttm")
        pct = item.get("pe_percentile")
        signal, reason = signal_for(name, item)
        code, fund_name, weight = FUND_BY_INDEX[name]
        amount = round(monthly * weight, 2)
        pe_text = f"{pe:.2f}" if isinstance(pe, (int, float)) else "-"
        pct_text = f"{pct:.2f}%" if isinstance(pct, (int, float)) else "-"
        rows.append(
            f"- {name}｜PE {pe_text}｜分位 {pct_text}｜{signal}｜{reason}"
        )
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
    short_total = round(short_base + paused_amount, 2)

    if mode == "evening":
        title = f"【定投晚间估值】{as_of} A股收盘后判断"
        when = (
            "本邮件对应晚间估值窗口（约 18:00~21:00）。\n"
            "A 股：今晚确认分位后，若「可买」，请在"
            f"下一个交易日 {next_trading_day_hint(date.fromisoformat(as_of))} "
            "09:00~14:55 打开招行 APP 下单（15 点前按当日净值）。\n"
            "美股：仅作参考；美股最终以明早 8~10 点更新后再定。"
        )
        focus = (
            f"A股可买：{('、'.join(buy_a) if buy_a else '无')}\n"
            f"明日下单提醒：{'有，见下方清单' if buy_a else '无（权益暂停，资金进短债）'}"
        )
    elif mode == "morning":
        title = f"【定投早间行动】{as_of} 今日 15 点前下单清单"
        when = (
            "本邮件对应早间行动窗口（约 08:00~10:00）。\n"
            "美股：按昨夜收盘后最终 PE 分位判断；若「可买」，今天 15 点前招行 APP 下单。\n"
            "A 股：若昨晚已判定可买，今天 09:00~14:55 补单；否则继续短债。"
        )
        focus = (
            f"美股可买：{('、'.join(buy_us) if buy_us else '无')}\n"
            f"A股可买（沿用当前快照）：{('、'.join(buy_a) if buy_a else '无')}\n"
            f"今日是否需要打开招行：{'是，15 点前下单' if (buy_a or buy_us) else '否，本月权益暂停'}"
        )
    else:
        title = f"【定投估值汇总】{as_of}"
        when = "手动触发：同时给出晚间估值与早间下单提示。"
        focus = (
            f"A股可买：{('、'.join(buy_a) if buy_a else '无')}；"
            f"美股可买：{('、'.join(buy_us) if buy_us else '无')}"
        )

    buy_block = "\n".join(buy_lines) if buy_lines else "  · 本期无权益买入"

    body = f"""{title}

生成时间：{now}
数据日期：{as_of}
月定投预算：{monthly:.0f} 元（按策略比例拆分；暂停部分转入短债）

【时点说明】
{when}

【本期结论】
{focus}

【四指数估值】
{chr(10).join(rows)}

【建议操作】
权益买入：
{buy_block}
短债底仓：{short_name}（{short_code}）约 {short_total:.2f} 元
  （含固定短债 {short_base:.2f} + 权益暂停转入 {paused_amount:.2f}）

【执行提醒】
1. 场外联接基金：交易日 15:00 前下单，按当日收盘净值成交，不必卡尾盘。
2. 以招行 APP 实际申购状态为准；暂停申购则改短债。
3. 本邮件仅研究提醒，不构成投资建议，也不会自动下单。

— My Fund AI Assistant
"""
    subject_tag = "可买" if (buy_a or buy_us) else "全部暂停"
    subject = f"{title}｜{subject_tag}"
    return subject, body


def env_float(name: str, default: float) -> float:
    """Read float env var; treat missing/blank as default (GitHub Secrets often inject '')."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return float(raw)


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
    with smtplib.SMTP_SSL(cfg["host"], port, context=context) as server:
        server.login(cfg["user"], cfg["password"])
        server.sendmail(cfg["from"], [cfg["to"]], msg.as_string())
    print(f"已发送至 {masked}")


def main() -> None:
    parser = argparse.ArgumentParser(description="发送定投时点邮件提醒")
    parser.add_argument(
        "--mode",
        choices=("evening", "morning", "both"),
        default="both",
        help="evening=晚间A股估值；morning=早间下单；both=两段合并",
    )
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
        help="只打印邮件内容，不真正发送",
    )
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    if not snapshot_path.is_absolute():
        snapshot_path = ROOT / snapshot_path
    snapshot = load_snapshot(snapshot_path)

    modes = ("evening", "morning") if args.mode == "both" else (args.mode,)
    for mode in modes:
        subject, body = build_body(mode, snapshot, args.monthly)
        send_email(subject, body, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
