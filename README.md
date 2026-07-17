# My Fund AI Assistant

一个面向长期指数定投、持仓记录与投资复盘的 AI 助手。

## 当前持仓

<!-- PORTFOLIO_STATUS_START -->
> 自动更新时间：**2026-07-17 12:02:34 CST**
> 建仓本金：**¥10,000.00** · 已投入：**¥2,000.00** · 整体建仓进度：**20.00%**
> 🟡 权益均暂停新增/高估观察；短债本期不催补（012773 长期还差 ¥3,100.00）
> 状态灯：🟢 可买/启动仓/可建仓 · 🟠 止盈观察 · 🟡 观望/暂停/溢价暂缓 · ⚪ 等待数据
> 说明：当前投入占比 = 单项已投入金额 ÷ 1万元建仓本金；目标金额 = 建仓本金 × 目标仓位。

| 基金 | 代码 | 已投入 | 目标仓位 | 目标金额 | 当前投入占比 | 还差目标金额 | 今日状态 |
|---|---:|---:|---:|---:|---:|---:|---|
| 嘉实超短债债券A | `012773` | ¥2,000.00 | 51.00% | ¥5,100.00 | **20.00%** | ¥3,100.00 | 本期不补满 |

### 权益信号速览

- 沪深300：高估观察，当前无持仓无需止盈，1年分位72.4%
- 中证500：高估观察，当前无持仓无需止盈，1年分位85.6%
- 标普500：高估观察，当前无持仓无需止盈，溢价4.82%，1年分位100.0%
- 纳斯达克100：仅参考·不自动买，溢价6.71%，1年分位无统计

### 今日判断依据

- `012773`：短债不看PE；第1月计划金额约 ¥1,020.00，当前已投入 ¥2,000.00，不建议今天一次补足 ¥3,100.00。

## 今日权益估值（4支）

> PE 数据用于判断指数贵不贵；场外基金按当日净值成交，数据日期以指数实际更新日为准。

| 标的 | 场内代码 | 场外基金 | PE-TTM | 10年分位 | 1年分位 | QDII溢价 | 数据日期 | 今日判断 |
|---|---:|---:|---:|---:|---:|---:|---|---|
| 沪深300 | `510300` | `460300` | 13.56 | 80.06% | 72.43% | - | 2026-07-16 | 高估观察，当前无持仓无需止盈 |
| 中证500 | `510500` | `160119` | 30.18 | 85.78% | 85.60% | - | 2026-07-16 | 高估观察，当前无持仓无需止盈 |
| 标普500 | `513500` | `050025` | 32.41 | 93.33% | 100.00% | 4.82% | 2026-07-16 | 高估观察，当前无持仓无需止盈 |
| 纳斯达克100 | `159941` | `016452` | 32.54 | 无统计分位 | 无统计分位 | 6.71% | 2026-07-17 | 仅参考·不自动买 |

> A股近10年分位为主策略；近1年分位用于启动仓（≤30%且未满目标仓15%可先建小仓）。标普用 Multpl 指数PE，四层校验通过才可交易判断。纳指 PE 来自 QQQ（stockanalysis/yfinance）**仅供参考**；样本不足时分位显示「无统计分位」。无持仓时高估只观察、不提示止盈。爬虫失败严禁用过期缓存做买卖。QDII溢价＞2%暂缓买入。1万元本金启动仓上限约：沪深300 405 / 中证500 165 / 标普500 120 元。

> 数据状态：market_snapshot.json 已加载；标普PE已核验（Multpl，2026-07-16）；纳指参考PE 32.54（QQQ，不交易）。AI 只提供研究建议，不自动下单。
<!-- PORTFOLIO_STATUS_END -->
持仓源文件：[`config/portfolio_holdings.json`](config/portfolio_holdings.json)

## 项目能力

- 维护保守型指数定投与再平衡政策
- 记录真实持仓与交易日记
- 刷新基金净值、申购状态、指数 PE 和建仓计划
- 通过 MCP 查询只读行情、账户和持仓数据
- 生成定投计划、复盘建议和风险提示

## MCP 查询

项目默认连接上游 Vibe-Trading 的 `vibe-trading-mcp`，只开放配置文件中的只读工具。先确保已经安装上游项目并且命令可用：

```powershell
pip install -r requirements-mcp.txt
```

查询市场数据示例：

```powershell
python scripts/query_mcp.py get_market_data --arguments '{"codes":["AAPL.US"],"start_date":"2026-07-01","end_date":"2026-07-16"}'
```

读取已配置交易连接器的持仓示例：

```powershell
python scripts/query_mcp.py trading_positions
```

MCP 配置：[`config/mcp_servers.json`](config/mcp_servers.json)。场外基金 `012773` 的已投入金额仍以本项目持仓账本为准，避免把不存在的券商连接器数据当成事实。

## 使用

```powershell
pip install -r requirements-data.txt
python scripts/refresh_market_snapshot.py --principal 10000
python scripts/update_portfolio_readme.py
python scripts/trading_calendar.py
python scripts/us_pe.py
python scripts/record_holding.py show
python scripts/record_holding.py buy --fund 460300 --amount 270 --note "定投"
python scripts/record_holding.py sell --fund 460300 --proceeds 90 --cost 80 --note "止盈1/3"
# 或按份额扣成本：--proceeds 90 --shares 10
# 幂等（北京时间 CST）：同日+同基金+同金额/份额+同备注 重复提交会被拒绝；备注不同=不同交易
# 可加 --tx-id；确需重复入账用 --force-duplicate
python scripts/send_trade_alert_email.py --dry-run
python app/ai_assistant.py --mode plan --input "本月可投入300元，510300估值分位32%"
```

买入/止盈后请用 `record_holding.py` 更新账本，README 持仓进度才会跟着变。
同时给出 `--amount` 与 `--shares/--nav` 时，允许约 `max(0.02元, |金额|×0.5%)` 的差额，用于申购费、四舍五入和小额费用，并不要求账本金额与份额×净值完全一致。

定时刷新计划约 **09:00 / 21:00 CST**，但 GitHub Actions 定时器无法保证整点启动（可能延迟数分钟到更久）。若上午要赶招行操作，建议约 **10:00 CST** 前到仓库 Actions 确认当日 run 是否成功；失败时 Job Summary 会写明原因（不等于「今天无信号」）。

## 目录

- `config/portfolio_policy.json`：程序可读取的投资政策
- `config/portfolio_holdings.json`：个人持仓账本
- `config/mcp_servers.json`：MCP 服务和只读工具白名单
- `app/ai_assistant.py`：AI 规划、日记和复盘入口
- `app/mcp_client.py`：MCP stdio 客户端
- `scripts/`：行情刷新、MCP 查询和 README 自动更新脚本
- `journal/`：交易日记
- `reports/`：AI 报告
- `data/`：行情和账户数据
- `integrations/upstream/`：只读上游项目副本

## 上游项目

- [AI Berkshire](https://github.com/xbtlin/ai-berkshire)
- [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)

项目主页：[Kiana-QvQ/My-Fund-AI-Assistant](https://github.com/Kiana-QvQ/My-Fund-AI-Assistant)
