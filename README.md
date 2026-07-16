# My Fund AI Assistant

一个面向长期指数定投的 AI 投资规划与交易日记项目。

## 当前能力

- 维护保守型指数定投与再平衡政策
- 生成定投计划和复盘建议
- 整理交易日记
- 检查交易是否偏离既定规则
- 通过两个互补的上游项目增强研究和复盘，避免修改上游代码

## 目录

- `docs/PORTFOLIO_POLICY.md`：完整投资策略
- `config/portfolio_policy.json`：程序可读取的策略配置
- `app/ai_assistant.py`：AI 规划、日记和复盘入口
- `journal/`：个人交易日记
- `reports/`：AI 报告
- `integrations/upstream/`：只读上游项目副本
- `config/project_integrations.json`：启用项目和安全边界

## 使用

程序会自动读取本机 `~/.codex/config.toml` 中的模型和 `base_url`，
并通过 `~/.codex/auth.json` 获取 API Key。也可以使用环境变量覆盖。
Key 不会写入项目文件：

```powershell
python app/ai_assistant.py --mode plan --input "本月可投入300元，510300估值分位32%"
python app/ai_assistant.py --mode journal --input journal/daily/2026-07-16-template.md
python app/ai_assistant.py --mode review --input reports/latest-trade.md
python app/ai_assistant.py --mode plan --input "本月可投入300元" --output reports/2026-07-plan.md
```

AI 默认只生成建议，不自动下单，所有交易必须人工确认。

## 上游项目

- [AI Berkshire](https://github.com/xbtlin/ai-berkshire)
- [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)

项目主页：[Kiana-QvQ/My-Fund-AI-Assistant](https://github.com/Kiana-QvQ/My-Fund-AI-Assistant)
