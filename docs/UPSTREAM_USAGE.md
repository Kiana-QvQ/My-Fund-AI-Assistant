# 两个上游项目使用手册

本手册基于当前本地副本整理。两个目录均为只读上游副本，不要在其中写入
个人配置、交易日记或报告。

## 0. 本机状态

- Python：3.11
- Docker：已安装
- Node.js：已安装
- Poetry：未安装
- npm PowerShell 脚本受执行策略限制时，使用 `npm.cmd`

建议把运行环境放在本项目的 `runtime/` 目录中，或直接使用 Docker，不要在
`integrations/upstream/` 内创建虚拟环境。

## 1. AI Berkshire

### 定位

它不是一个需要启动的交易软件，而是一组给 Codex/Claude Code 使用的投研
Skills。适合：

- 投资论文
- 组合复盘
- 论文漂移
- 新闻归因
- 公司、行业和财报研究

它不负责行情数据库、券商账户或自动下单。

### 你的使用方式

先在 Codex 中安装对应的 Skills。当前副本运行
`python scripts/sync-codex-skills.py --check` 时发现：

```text
codex-skills/deep-company-series/SKILL.md
codex-skills/investment-team/SKILL.md
```

与源文件不同。因此不要直接执行会改写上游副本的安装脚本。可以先使用
当前已经生成的 `codex-skills/`，或在确认后再单独生成一份临时安装副本。

适合你的任务：

```text
使用 portfolio-review 检查我的指数定投组合是否偏离 51/27/11/8/3 目标仓位
使用 thesis-tracker 为 510300、510500、513500、159941 建立长期投资论文
使用 thesis-drift 对比本季度和上季度的组合投资论文
使用 news-pulse 分析本周影响组合的重大新闻
使用 investment-checklist 检查是否应该执行本月定投
```

注意：它的示例主要面向公司和股票研究。对你的 ETF 组合，应明确告诉它：

1. 这是指数基金，不要把 ETF 当作单一公司分析。
2. 不得擅自改变 `config/portfolio_policy.json` 中的目标仓位。
3. 缺少估值分位、净值、溢价率或费用数据时必须标记“数据缺失”。

## 2. Vibe-Trading

### 定位

这是两个项目中最适合你当前目标的主工具，负责：

- 市场数据查询
- 自然语言研究
- 策略回测
- 交易记录分析
- Shadow Account 行为复盘
- 纸面账户和只读账户连接
- Web UI、CLI 和 MCP 接入

它最适合承接 `journal/`、`data/` 和 `reports/` 的工作流。

### 推荐启动方式：Docker

这样不会把依赖安装到上游目录：

```powershell
cd "E:\My Fund AI Assistant\integrations\upstream\vibe-trading"
Copy-Item agent\.env.example agent\.env
notepad agent\.env
docker compose up --build
```

在 `agent/.env` 中只启用一个 LLM 提供商。例如使用 OpenAI-compatible
服务时，设置对应的 `LANGCHAIN_PROVIDER`、`LANGCHAIN_MODEL_NAME`、
`<PROVIDER>_API_KEY` 和 `<PROVIDER>_BASE_URL`。

启动后访问：

```text
http://localhost:8899
```

停止服务：

```powershell
docker compose down
```

不要使用 `docker compose down -v`，否则会删除该项目保存的运行数据、会话、
回测和上传文件卷。

### 本地安装方式

只有在需要调试源码时才使用。由于本机没有 Poetry，建议用独立环境：

```powershell
cd "E:\My Fund AI Assistant"
py -3.11 -m venv runtime\vibe-venv
runtime\vibe-venv\Scripts\python.exe -m pip install --upgrade pip
runtime\vibe-venv\Scripts\python.exe -m pip install -e "integrations\upstream\vibe-trading"
```

然后从上游目录运行：

```powershell
cd "E:\My Fund AI Assistant\integrations\upstream\vibe-trading"
..\..\runtime\vibe-venv\Scripts\vibe-trading.exe init
..\..\runtime\vibe-venv\Scripts\vibe-trading.exe run -p "..."
```

如果 editable 安装在上游目录生成额外构建文件，保留这些文件，不要手动修改
上游源码；正式使用优先选择 Docker。

### 你的交易日记工作流

把券商导出的 CSV 放到本项目的 `data/`，不要直接放入上游目录，然后运行：

```powershell
vibe-trading --upload "E:\My Fund AI Assistant\data\trades_export.csv"
vibe-trading run -p "Analyze my trading behavior, extract my shadow strategy, and compare it with my actual trades"
```

在交互式 TUI 中也可以使用：

```text
/journal E:\My Fund AI Assistant\data\trades_export.csv
/shadow E:\My Fund AI Assistant\data\trades_export.csv
```

Shadow Account 的正确顺序是：

1. 解析交易记录
2. 分析持有天数、胜率、回撤、过度交易和追涨杀跌
3. 提取你的实际交易规则
4. 运行影子策略回测
5. 查看 HTML/PDF 差异报告

它分析的是“你过去实际怎么交易”，不是直接替你决定下一笔交易。

### 你的基金组合查询

Vibe-Trading 的 A 股工具要求使用带市场后缀的代码，例如：

```text
000001.SZ
600519.SH
```

你的基金代码需要先转换为对应市场后缀后再查询。执行计划时应把以下内容
一起提供给 AI：

```text
目标仓位：51/27/11/8/3
当前仓位：...
本月可投入金额：...
估值分位：...
QDII 溢价率：...
查询日期：...
```

推荐提示词：

```text
只做研究和纸面计划，不下单。
根据 data/portfolio_snapshot.csv、config/portfolio_policy.json 和最新行情，
检查我的组合是否偏离目标仓位，判断本月每个标的应该正常定投、暂停、加倍
还是等待，并列出每个判断所需的数据来源和缺失项。
```

### Vibe-Trading 的安全边界

第一阶段只使用：

- `run`
- `backtest`
- `analyze_trade_journal`
- `extract_shadow_strategy`
- `run_shadow_backtest`
- `render_shadow_report`
- 账户、持仓、订单的只读查询

暂时不要配置真实券商下单、`VIBE_TRADING_ENABLE_SHELL_TOOLS=1` 或真实交易
mandate。先使用纸面账户和本地报告。

## 3. 推荐组合

```text
My Fund AI Assistant
  ├─ 投资政策：config/portfolio_policy.json
  ├─ 日记和数据：journal/ data/
  ├─ AI Berkshire：论文、组合复盘、新闻归因
  ├─ Vibe-Trading：行情、回测、日记行为分析、Shadow Account
```

建议顺序：

1. 先使用 AI Berkshire 复核投资政策和投资论文。
2. 再用 Vibe-Trading 导入一份历史交易 CSV，生成行为报告。
3. 把报告复制到本项目 `reports/`，由本项目的 `review` 模式检查是否违反
   你的政策。
4. 连续运行一个月后，再考虑接入本地行情和估值数据。
5. 在纸面账户验证前，不配置任何真实下单能力。
