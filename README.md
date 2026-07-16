# My Fund AI Assistant

一个面向长期指数定投的 AI 投资规划与交易日记项目。

## 当前能力

- 维护保守型指数定投与再平衡政策
- 生成定投计划和复盘建议
- 整理交易日记
- 检查交易是否偏离既定规则
- 隔离保存三个上游 AI 交易项目，避免修改上游代码

## 目录

- `docs/PORTFOLIO_POLICY.md`：完整投资策略
- `config/portfolio_policy.json`：程序可读取的策略配置
- `app/ai_assistant.py`：AI 规划、日记和复盘入口
- `journal/`：个人交易日记
- `reports/`：AI 报告
- `integrations/upstream/`：只读上游项目副本

## 使用

配置 `config/.env.example` 后运行：

```powershell
python app/ai_assistant.py --mode plan --input "本月可投入300元，510300估值分位32%"
python app/ai_assistant.py --mode journal --input journal/daily/2026-07-16-template.md
python app/ai_assistant.py --mode review --input reports/latest-trade.md
```

AI 默认只生成建议，不自动下单，所有交易必须人工确认。

## 上游项目

- [AI Berkshire](https://github.com/xbtlin/ai-berkshire)
- [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)
- [AI Hedge Fund](https://github.com/virattt/ai-hedge-fund)

项目主页：[Kiana-QvQ/My-Fund-AI-Assistant](https://github.com/Kiana-QvQ/My-Fund-AI-Assistant)
