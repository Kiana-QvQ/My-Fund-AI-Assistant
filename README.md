# My Fund AI Assistant

一个面向长期指数定投、持仓记录与投资复盘的 AI 助手。

## 当前持仓

> 更新时间：**2026-07-16**
> 口径：以下金额为已记录的实际投入金额；净值、份额和浮动盈亏将在行情数据刷新后补充。
> AI 只提供研究和计划建议，不会自动下单。

| 基金 | 代码 | 资产类别 | 已投入 | 组合目标 | 当前记录 |
|---|---:|---|---:|---:|---|
| 嘉实超短债债券 A | `012773` | 短债基金 | **¥2,000.00** | 51% | 已持有 |

### 持仓快照

```text
012773  嘉实超短债债券A
已投入    ¥2,000.00
目标仓位  51%
状态      已买入 · 低波动底仓
```

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
```

## 目录

- `config/portfolio_policy.json`：程序可读取的投资政策
- `config/portfolio_holdings.json`：个人持仓账本
- `config/mcp_servers.json`：MCP 服务和只读工具白名单
- `app/ai_assistant.py`：AI 规划、日记和复盘入口
- `app/mcp_client.py`：MCP stdio 客户端
- `scripts/`：行情刷新与 MCP 查询脚本
- `journal/`：交易日记
- `reports/`：AI 报告
- `data/`：行情和账户数据
- `integrations/upstream/`：只读上游项目副本

## 上游项目

- [AI Berkshire](https://github.com/xbtlin/ai-berkshire)
- [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)

项目主页：[Kiana-QvQ/My-Fund-AI-Assistant](https://github.com/Kiana-QvQ/My-Fund-AI-Assistant)
