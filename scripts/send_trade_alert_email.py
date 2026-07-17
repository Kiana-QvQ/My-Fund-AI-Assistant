"""Send DCA (weekly) and build (event) emails — never merged into one stream.

Kinds:
- weekly_dca: Thursday plan email (always when there is any non-zero weekly buy
  or explicit pause notice for watched indexes)
- event_dca: mid-week multiplier / pause / resume changes
- event_build: build tier become-buyable / lose-buyable / tier change

US rules (fail-closed): SPX needs Multpl verification; NDX never auto.
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

from alert_state import (  # noqa: E402
    diff_fingerprint,
    load_alert_state,
    save_alert_state,
)
from build_state_machine import (  # noqa: E402
    FRAC_TO_STATE,
    advance_machine,
    confirm_days,
    fingerprint_from_machines,
    is_buyable,
    state_from_fraction,
)
from investment_plan import (  # noqa: E402
    allocate_dca_plan,
    build_summary_line,
    dca_summary_line,
    fingerprint_dca,
    resolve_build_line,
    resolve_dca_line,
)
from policy_rules import load_policy  # noqa: E402
from trading_calendar import (  # noqa: E402
    is_a_share_trading_day,
    next_a_share_trading_day,
    resolve_order_window,
    today_cst,
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
WATCH = ("沪深300", "中证500", "标普500")


def mask_email(address: str) -> str:
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


def actual_dca_spent(month_key: str, policy: dict | None = None) -> float:
    """Sum recorded **DCA** buys this month (ledger truth, not emails).

    Only counts purpose=dca, or note containing 定投/dca.
    Bootstrap / other buys on the same funds do not consume the DCA budget.
    """
    if not HOLDINGS_PATH.is_file():
        return 0.0
    from investment_plan import dca_config  # local import for sleeve codes

    pol = policy or load_policy()
    sleeve_codes = {
        str(s.get("fund_code"))
        for s in (dca_config(pol).get("sleeves") or [])
        if s.get("fund_code")
    }
    doc = json.loads(HOLDINGS_PATH.read_text(encoding="utf-8"))
    total = 0.0
    for tx in doc.get("transactions") or []:
        if tx.get("side") != "buy":
            continue
        if str(tx.get("trade_date") or "")[:7] != month_key:
            continue
        fund = str(tx.get("fund_code") or "")
        if sleeve_codes and fund not in sleeve_codes:
            continue
        purpose = str(tx.get("purpose") or "").strip().lower()
        note = str(tx.get("note") or "")
        # Prefer explicit purpose; avoid matching「非定投」via substring「定投」.
        if purpose == "dca":
            is_dca = True
        elif purpose in ("bootstrap", "build", "other", "take_profit"):
            is_dca = False
        else:
            note_l = note.lower()
            is_dca = (
                ("定投" in note and "非定投" not in note)
                or ("dca" in note_l and "非dca" not in note_l and "non-dca" not in note_l)
            )
        if not is_dca:
            continue
        total += float(tx.get("amount") or tx.get("cost_delta") or 0.0)
    return round(total, 2)


def weekly_dca_due(today) -> bool:
    """Send the Thursday plan on Thursday or its first following A-share day."""
    if today.weekday() == 3:
        return True
    days_since_thursday = (today.weekday() - 3) % 7
    thursday = today - timedelta(days=days_since_thursday)
    if thursday >= today:
        return False
    try:
        if is_a_share_trading_day(thursday):
            return False
        return next_a_share_trading_day(thursday) == today
    except Exception:
        return False


def collect_dca(
    snapshot: dict,
    policy: dict,
    *,
    today=None,
    month_spent: float = 0.0,
) -> list[dict]:
    indexes = snapshot.get("indexes", {})
    equity_lines = []
    for name in WATCH:
        item = indexes.get(name, {})
        equity_lines.append(
            resolve_dca_line(
                name,
                item.get("pe_percentile"),
                premium=item.get("qdii_premium"),
                drawdown_from_52w_high=item.get("drawdown_from_52w_high"),
                policy=policy,
                verified=item.get("verified"),
                tradeable=item.get("tradeable"),
            )
        )
    # 纳指 multiplier line (excluded → 0) so allocate can redirect weight
    ndx = indexes.get("纳斯达克100", {})
    equity_lines.append(
        resolve_dca_line(
            "纳斯达克100",
            ndx.get("pe_percentile"),
            premium=ndx.get("qdii_premium"),
            drawdown_from_52w_high=ndx.get("drawdown_from_52w_high"),
            policy=policy,
            verified=ndx.get("verified"),
            tradeable=ndx.get("tradeable"),
        )
    )
    lines = allocate_dca_plan(
        equity_lines,
        policy=policy,
        today=today or today_cst(),
        month_spent=month_spent,
    )
    # Attach PE for equity sleeves
    for line in lines:
        idx = line["name"]
        if idx in indexes:
            item = indexes[idx]
            line["pe"] = item.get("pe_ttm")
            line["pct_10y"] = item.get("pe_percentile")
    return lines


def collect_build(snapshot: dict, policy: dict) -> list[dict]:
    indexes = snapshot.get("indexes", {})
    held = holdings_cost()
    principal = building_principal()
    lines = []
    for name in WATCH:
        item = indexes.get(name, {})
        code, fund_name, weight = FUND_BY_INDEX[name]
        line = resolve_build_line(
            name,
            item.get("pe_percentile"),
            percentile_1y=item.get("pe_percentile_1y"),
            drawdown_from_52w_high=item.get("drawdown_from_52w_high"),
            premium=item.get("qdii_premium"),
            policy=policy,
            verified=item.get("verified"),
            tradeable=item.get("tradeable"),
            held_cost=float(held.get(code, 0) or 0),
            target_amount=principal * weight,
            drawdown_status=item.get("drawdown_status"),
            pe_status=item.get("pe_status") or item.get("status"),
        )
        line["fund_code"] = code
        line["fund_name"] = fund_name
        line["pct_10y"] = item.get("pe_percentile")
        line["pct_1y"] = item.get("pe_percentile_1y")
        line["dd"] = item.get("drawdown_from_52w_high_pct")
        line["premium_pct"] = item.get("qdii_premium_pct")
        if line.get("premium_pct") is None and isinstance(
            item.get("qdii_premium"), (int, float)
        ):
            line["premium_pct"] = float(item["qdii_premium"]) * 100
        lines.append(line)
    return lines


def _write_github_summary(markdown: str) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary:
        return
    with open(summary, "a", encoding="utf-8") as handle:
        handle.write(markdown)
        if not markdown.endswith("\n"):
            handle.write("\n")


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
            + "。请在本地 .env 或 GitHub Secrets 配置。"
        )
    return {
        "to": to_addr,
        "user": smtp_user,
        "password": smtp_pass,
        "host": smtp_host,
        "port": smtp_port,
        "from": mail_from,
    }


def send_email(subject: str, body: str, dry_run: bool = False, *, retries: int = 2) -> None:
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


def build_dca_email(
    *,
    title: str,
    lines: list[dict],
    timing: dict[str, str],
    policy: dict,
    changes: list[str] | None = None,
) -> tuple[str, str]:
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")
    ops = [ln for ln in lines if ln["weekly"] > 0]
    paused = [ln for ln in lines if ln["paused"]]
    subject = f"【定投计划】{timing['order_date']}｜{title}"
    if ops:
        names = "、".join(ln["name"] for ln in ops)
        subject = f"{subject}｜操作：{names}"
    elif paused:
        subject = f"{subject}｜本周暂停权益定投"

    op_block = (
        "\n".join(
            f"  · {ln['fund_name']}（{ln['fund_code']}）本周约 {ln['weekly']:.2f} 元"
            f"（月额度 {ln['monthly']:.0f}｜倍率 {ln['multiplier'] * 100:.0f}%）"
            f"｜{ln['reason']}"
            for ln in ops
        )
        or "  · 无（本周各标的定投均为 0）"
    )
    pause_block = (
        "\n".join(f"  · {ln['name']}：{ln['reason']}" for ln in paused)
        or "  · 无"
    )
    change_block = (
        "\n".join(f"- {c}" for c in changes) if changes else "- （固定周报，无额外变更摘要）"
    )
    total_week = sum(float(ln["weekly"]) for ln in lines)
    total_month = float((lines[0].get("month_target_total") if lines else 0) or 0)
    thursdays_left = int((lines[0].get("thursdays_left") if lines else 0) or 0)
    month_spent = float((lines[0].get("month_spent") if lines else 0) or 0)
    month_remaining = float((lines[0].get("month_remaining") if lines else 0) or 0)

    body = f"""{subject}

生成时间：{now}
邮件类型：定投（与建仓邮件分离）
signal_date：{timing["signal_date"]}
order_date：{timing["order_date"]}
cutoff_time：{timing["cutoff_time"]}
请在 {timing["order_date"]} 的 15:00 前于银行 APP 提交场外 A 类申购。

【规则】
{dca_summary_line(policy)}
工资日资金留存账户，由每周四计划统一调度，无单独工资日邮件。

【本月预算】
- 本月计划总额：{total_month:.2f} 元（组合基础 300 / 封顶 1000）
- 本月账本已记定投：{month_spent:.2f} 元；剩余可投：{month_remaining:.2f} 元
- 本月剩余周四：{thursdays_left}；本周合计约 {total_week:.2f} 元
- 说明：仅统计 purpose=dca / 备注含「定投」的买入；建仓等其它买入不扣定投额度。

【变更摘要】
{change_block}

【本周操作】
{op_block}

【暂停/观望】
{pause_block}

【说明】
- 组合月总额按目标仓位分给 5 支；权益暂停/纳指份额并入短债。
- 正常估值固定 100%；低估才 200%/300% 加码；总额严格 ≤1000。
- 本周金额 = 本月剩余预算 ÷ 剩余周四（含本周），避免 5 周月超支。
- 申购完成后请告知助手记账（`record_holding.py buy --purpose dca`），预算才会扣减。
- 仅研究提醒，不自动下单。

— My Fund AI Assistant
"""
    return subject, body


def build_build_email(
    *,
    lines: list[dict],
    timing: dict[str, str],
    policy: dict,
    changes: list[str],
) -> tuple[str, str]:
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")
    changed_names = {
        c.split(":", 1)[0].strip()
        for c in changes
        if ":" in c
    }
    focus = [ln for ln in lines if ln["name"] in changed_names] or lines
    active = [ln for ln in focus if ln.get("active")]
    subject = f"【建仓事件】{timing['signal_date']}｜状态变更"
    if active:
        subject += "｜可建：" + "、".join(ln["name"] for ln in active)
    else:
        labels = "、".join(
            str(ln.get("state") or ln.get("tier_label")) for ln in focus[:3]
        )
        subject += f"｜{labels}" if labels else "｜条件失效/不可买"

    def _fmt_metrics(ln: dict) -> str:
        pct10 = ln.get("pct_10y")
        pct1 = ln.get("pct_1y")
        dd = ln.get("dd")
        prem = ln.get("premium_pct")
        parts = [
            f"10年PE分位 {pct10:.2f}%"
            if isinstance(pct10, (int, float))
            else "10年PE分位 —",
            f"1年PE分位 {pct1:.2f}%"
            if isinstance(pct1, (int, float))
            else "1年PE分位 —",
            f"52周回撤 {dd:.2f}%" if isinstance(dd, (int, float)) else "52周回撤 —",
            f"QDII溢价 {prem:.2f}%"
            if isinstance(prem, (int, float))
            else "QDII溢价 —",
        ]
        return "；".join(parts)

    detail_block = (
        "\n".join(
            (
                f"  · {ln['name']}｜{ln['fund_name']}（{ln['fund_code']}）\n"
                f"    状态：{ln.get('state') or ln.get('tier_label')}\n"
                f"    指标：{_fmt_metrics(ln)}\n"
                f"    本次建议金额：{float(ln.get('amount') or 0):.2f} 元"
                f"{'（可研究申购）' if ln.get('active') else '（不建议买入）'}\n"
                f"    人工确认：{'需要' if ln.get('needs_human_confirm', True) else '否'}｜"
                f"{ln.get('reason') or ''}"
            )
            for ln in focus
        )
        or "  · （无明细）"
    )
    change_block = "\n".join(f"- {c}" for c in changes) or "- （无）"
    body = f"""{subject}

生成时间：{now}
邮件类型：建仓事件（不与周度定投合并）
signal_date：{timing["signal_date"]}
order_date：{timing["order_date"]}
cutoff_time：{timing["cutoff_time"]}
请在 {timing["order_date"]} 的 15:00 前于银行 APP 提交场外 A 类申购（若建议买入）。

【规则】
{build_summary_line(policy)}

【状态变更】
{change_block}

【标的明细】
{detail_block}

【记账】
- 定投买入：`record_holding.py buy --purpose dca`
- 建仓买入：`record_holding.py buy --purpose build`
- 同一状态持续不变、或仅买入金额/距目标进度变化，不会重复发信。
- 不因「尚未买满」每日催促。

【说明】
- 场外未知价：估值日 ≠ 净值确认日属正常。
- 仅研究提醒，不自动下单。

— My Fund AI Assistant
"""
    return subject, body


def main() -> None:
    parser = argparse.ArgumentParser(
        description="定投周报 / 定投事件 / 建仓事件 分开发送"
    )
    parser.add_argument("--snapshot", default=str(SNAPSHOT_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--mode",
        choices=("auto", "weekly_dca", "event", "force_dca", "force_build"),
        default="auto",
        help="auto=周四发定投周报+检测事件；event=仅事件；weekly_dca=强制周报",
    )
    parser.add_argument(
        "--persist-state",
        action="store_true",
        help="把最新指纹写入 data/alert_state.json（Actions 应开启）",
    )
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    if not snapshot_path.is_absolute():
        snapshot_path = ROOT / snapshot_path
    if not snapshot_path.is_file():
        raise SystemExit(f"找不到快照文件: {snapshot_path}")

    snapshot = load_snapshot(snapshot_path)
    policy = load_policy()
    today = today_cst()
    timing = resolve_order_window("morning", as_of=snapshot.get("as_of"), today=today)
    is_thursday = weekly_dca_due(today)

    state = load_alert_state()
    month_key = today.strftime("%Y-%m")
    dca_month = state.get("dca_month") or {}
    month_spent = actual_dca_spent(month_key, policy)

    dca_lines = collect_dca(
        snapshot, policy, today=today, month_spent=month_spent
    )
    build_lines = collect_build(snapshot, policy)
    new_dca_fp = fingerprint_dca(dca_lines)

    old_dca = state.get("dca") or {}
    old_machines = state.get("build_machines") or {}
    # Migrate legacy fingerprint → machine baseline (no email storm).
    if not old_machines and state.get("build"):
        legacy_label_map = {
            "溢价暂缓": "QDII溢价阻断",
            "QDII溢价阻断": "QDII溢价阻断",
            "止盈观察": "止盈观察",
            "数据源失败": "数据源失败",
            "不可买": "不可买",
            "正式建仓 100%": "正式建仓 100%",
            "正式小额底仓 50%": "正式小额底仓 50%",
            "宽松观测仓 25%": "宽松观测仓 25%",
            # Pre-rename labels
            "正式小额底仓": "正式建仓 100%",
            "宽松观测仓": "宽松观测仓 25%",
        }
        for name, fp in (state.get("build") or {}).items():
            legacy = None
            if isinstance(fp, dict):
                if fp.get("state"):
                    legacy = str(fp["state"])
                elif isinstance(fp.get("fraction"), (int, float)) and float(fp["fraction"]) > 0:
                    legacy = state_from_fraction(float(fp["fraction"]))
                elif fp.get("tier_label"):
                    legacy = legacy_label_map.get(
                        str(fp["tier_label"]), str(fp["tier_label"])
                    )
            if legacy:
                old_machines[name] = {
                    "current_state": legacy,
                    "candidate_state": None,
                    "candidate_count": 0,
                    "last_notified_state": legacy,
                    "last_notified_at": state.get("updated_at"),
                }

    needed = confirm_days(policy)
    force_build = args.mode == "force_build"
    # Upgrade/recovery counters only advance on A-share trading days.
    count_observation = bool(is_a_share_trading_day(today))
    new_machines: dict = {}
    build_changes: list[str] = []
    notify_build = False
    for ln in build_lines:
        name = ln["name"]
        observed = ln.get("state") or ln.get("tier_label") or "不可买"
        machine, should_notify, change = advance_machine(
            old_machines.get(name),
            observed,
            confirm_needed=needed,
            force_notify=force_build,
            count_observation=count_observation,
        )
        new_machines[name] = machine
        ln["machine"] = machine
        ln["observed_state"] = observed
        confirmed = machine.get("current_state") or observed
        # Display confirmed state; keep observed for pending logs.
        ln["state"] = confirmed
        ln["tier_label"] = confirmed
        if machine.get("candidate_state"):
            ln["pending_confirm"] = (
                f"候选 {machine['candidate_state']} "
                f"({machine.get('candidate_count')}/{needed}"
                f"{'' if count_observation else ',休市不计日'})"
            )
            # Pending upgrade/recovery: do not advertise the unconfirmed tier.
            if confirmed != observed:
                if not is_buyable(confirmed):
                    ln["active"] = False
                    ln["amount"] = 0.0
                    ln["fraction"] = 0.0
                else:
                    state_to_frac = {v: k for k, v in FRAC_TO_STATE.items()}
                    conf_frac = state_to_frac.get(confirmed)
                    obs_frac = float(ln.get("fraction") or 0)
                    if conf_frac is not None and obs_frac > 0:
                        ln["amount"] = round(
                            float(ln.get("amount") or 0) * conf_frac / obs_frac, 2
                        )
                        ln["fraction"] = conf_frac
        if should_notify and change:
            build_changes.append(f"{name}: {change}")
            notify_build = True
            ln["state"] = machine.get("current_state") or observed
            ln["tier_label"] = ln["state"]

    dca_changes = diff_fingerprint(old_dca, new_dca_fp)
    first_run = not old_dca and not old_machines and not state.get("build")

    print(
        f"mode={args.mode} thursday={is_thursday} "
        f"dca_changes={len(dca_changes)} build_changes={len(build_changes)} "
        f"first_run={first_run} month_spent={month_spent}"
    )
    for ln in dca_lines:
        print(
            f"DCA {ln['name']}: mult={ln['multiplier']} "
            f"weekly={ln['weekly']} monthly={ln['monthly']} paused={ln['paused']}"
        )
    for ln in build_lines:
        pending = ln.get("pending_confirm") or ""
        print(
            f"BUILD {ln['name']}: state={ln.get('state')} "
            f"active={ln['active']} amount={ln['amount']} {pending}"
        )

    sent_weekly = False
    sent_event_dca = False
    email_cfg = (policy.get("dca") or {}).get("email") or {}
    weekly_enabled = bool(email_cfg.get("weekly_thursday", True))
    event_enabled = bool(email_cfg.get("event_on_change", True))
    max_dca_mails = int(email_cfg.get("max_dca_emails_per_month", 6))
    dca_mails_sent = (
        int(dca_month.get("emails_sent") or 0)
        if dca_month.get("year_month") == month_key
        else 0
    )

    def dca_quota_left() -> bool:
        if max_dca_mails <= 0:
            return False
        return dca_mails_sent < max_dca_mails

    send_weekly = (
        weekly_enabled
        and dca_quota_left()
        and (
            args.mode in ("weekly_dca", "force_dca")
            or (args.mode == "auto" and is_thursday)
        )
    )
    if send_weekly:
        subject, body = build_dca_email(
            title="周四定投周报",
            lines=dca_lines,
            timing=timing,
            policy=policy,
            changes=dca_changes or None,
        )
        send_email(subject, body, dry_run=args.dry_run)
        sent_weekly = True
        dca_mails_sent += 1

    # 周四周报与事件信可并存；受 event_on_change 与月度封顶约束
    send_event_dca = (
        event_enabled
        and dca_quota_left()
        and args.mode in ("event", "auto", "force_dca")
        and (args.mode == "force_dca" or dca_changes)
    )
    if send_event_dca and dca_changes:
        subject, body = build_dca_email(
            title="定投档位/倍率变更",
            lines=dca_lines,
            timing=timing,
            policy=policy,
            changes=dca_changes,
        )
        send_email(subject, body, dry_run=args.dry_run)
        sent_event_dca = True
        dca_mails_sent += 1
    elif (
        event_enabled
        and dca_changes
        and not dca_quota_left()
        and args.mode in ("event", "auto")
    ):
        print(
            f"定投事件信跳过：本月定投邮件已达上限 {max_dca_mails} 封"
            f"（已发 {dca_mails_sent}）"
        )

    send_build = args.mode in ("event", "auto", "force_build") and (
        force_build or notify_build
    )
    sent_build = False
    if send_build and (force_build or build_changes):
        subject, body = build_build_email(
            lines=build_lines,
            timing=timing,
            policy=policy,
            changes=build_changes or ["（手动 force_build）"],
        )
        send_email(subject, body, dry_run=args.dry_run)
        sent_build = True

    if (
        not sent_weekly
        and not sent_event_dca
        and not sent_build
        and args.mode != "force_build"
    ):
        print("跳过发送：无周四周报且无定投/建仓状态变更（或定投邮件达月上限）")
        _write_github_summary(
            "### 邮件跳过\n\n"
            "- 非周四周报，或定投/建仓状态无变化，或定投邮件已达月上限\n"
        )

    if args.persist_state and not args.dry_run:
        save_alert_state(
            {
                "dca": new_dca_fp,
                "build": fingerprint_from_machines(new_machines),
                "build_machines": new_machines,
                "dca_month": {
                    "year_month": month_key,
                    "spent": month_spent,
                    "emails_sent": dca_mails_sent,
                    "planned_monthly": float(
                        (dca_lines[0].get("month_target_total") if dca_lines else 0)
                        or 0
                    ),
                },
                "updated_at": datetime.now(CST).isoformat(timespec="seconds"),
            }
        )
        print("已更新 data/alert_state.json")
    elif args.persist_state and args.dry_run:
        print("dry-run：不写入 alert_state")


if __name__ == "__main__":
    main()
