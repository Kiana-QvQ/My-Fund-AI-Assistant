# My Fund AI Assistant

一个面向长期指数定投、持仓记录与投资复盘的 AI 助手。

## 当前持仓

<!-- PORTFOLIO_STATUS_START -->
> 自动更新时间：**2026-07-17 09:46:10 CST**
> 建仓本金：**¥10,000.00** · 已投入：**¥2,000.00** · 整体建仓进度：**20.00%**
> 🟡 观望（短债本期不催补）：短债不看 PE，第1月也不要求一次补满；012773 长期目标还差 ¥3,100.00
> 状态灯：🟢 可继续建仓 · 🟡 观望/不催补 · ⚪ 等待数据
> 说明：当前投入占比 = 单项已投入金额 ÷ 1万元建仓本金；目标金额 = 建仓本金 × 目标仓位。

| 基金 | 代码 | 已投入 | 目标仓位 | 目标金额 | 当前投入占比 | 还差目标金额 | 今日状态 |
|---|---:|---:|---:|---:|---:|---:|---|
| 嘉实超短债债券A | `012773` | ¥2,000.00 | 51.00% | ¥5,100.00 | **20.00%** | ¥3,100.00 | 本期不补满 |

### 今日判断依据

- `012773`：短债不看PE；第1月计划金额约 ¥1,020.00，当前已投入 ¥2,000.00，不建议今天一次补足 ¥3,100.00。

## 今日权益估值（4支）

> PE 数据用于判断指数贵不贵；场外基金按当日净值成交，数据日期以指数实际更新日为准。

| 标的 | 场内代码 | 场外基金 | PE-TTM | 历史分位 | 数据日期 | 今日判断 |
|---|---:|---:|---:|---:|---|---|
| 沪深300 | `510300` | `460300` | 13.56 | 63.89% | 2026-07-16 | 分位≥40%，暂停新增 |
| 中证500 | `510500` | `160119` | 30.18 | 64.32% | 2026-07-16 | 分位≥40%，暂停新增 |
| 标普500 | `513500` | `050025` | 27.50 | 76.00% | 2026-07-15 | 分位≥50%，暂停新增 |
| 纳斯达克100 | `159941` | `016452` | 33.60 | 72.00% | 2026-07-15 | 分位≥50%，暂停新增 |

> 美股 PE 使用 `config/us_pe_snapshot.json` 中的近10年滚动PE分位口径；不同网站口径可能不同，自动任务会保留数据日期和来源。

> 数据状态：market_snapshot.json 已加载。AI 只提供研究建议，不自动下单。
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
python app/ai_assistant.py --mode plan --input "本月可投入300元，510300估值分位32%"
python app/ai_assistant.py --mode journal --input journal/daily/2026-07-16-template.md
python app/ai_assistant.py --mode review --input reports/latest-trade.md
python app/ai_assistant.py --mode plan --input "本月可投入300元" --output reports/2026-07-plan.md
python scripts/refresh_market_snapshot.py --principal 10000
python scripts/update_portfolio_readme.py
```

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
