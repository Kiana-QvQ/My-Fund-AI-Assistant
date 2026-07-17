# AI Integration Design

## Boundary

The local project is the owner of investment policy, personal data, journal
entries, generated reports, and human approvals. The repositories under
`integrations/upstream/` are read-only reference copies.

AI may:

- turn policy and current observations into a proposed plan;
- draft a journal entry;
- review a completed trade against the policy;
- summarize evidence and list missing data.

AI may not:

- place, cancel, or modify broker orders;
- silently change target allocations or thresholds;
- treat an unverified price, valuation, or backtest as fact.

## Adapter strategy

1. `app/ai_assistant.py` is the stable project-facing interface.
2. A future adapter in `integrations/adapters/` may import data from Vibe-Trading
   or invoke an AI Berkshire workflow.
3. Data is copied into `data/` or passed as structured input; upstream source
   files are never edited.
4. AI output is saved under `reports/` only after a human review.
5. API keys are read from process environment variables or the local Codex auth
   file at runtime and are never saved into this repository.

## Suggested phases

### Phase 1: planning and journaling

Use `config/portfolio_policy.json`, the journal template, and the local AI
assistant. Keep all outputs advice-only.

### Phase 2: data and backtesting

Add a read-only adapter for prices, valuation percentiles, fund NAV, and QDII
premium. Use Vibe-Trading only for data, research, paper trading, and
backtesting. Store the raw input and timestamp with every report.

The project now includes a small MCP stdio client in `app/mcp_client.py`.
Install the optional dependencies from `requirements-mcp.txt`, then use
`scripts/query_mcp.py` with the read-only allowlist in
`config/mcp_servers.json`. Broker-backed position queries remain optional;
the local `config/portfolio_holdings.json` file is the source of truth for
manually recorded fund holdings such as `012773`.

### Phase 3: thesis and behavior review

Use AI Berkshire-style thesis tracking to compare the original investment
thesis with later evidence. Use Vibe-Trading's journal and Shadow Account
workflow to compare actual behavior with the policy, not to generate orders.

## Example

```powershell
$env:AI_BASE_URL = "https://api.openai.com/v1"
$env:AI_API_KEY = "replace-me"
$env:AI_MODEL = "replace-me"
python app/ai_assistant.py --mode plan --input "本月可投入300元，510300估值分位32%，当前组合短债53%"
python app/ai_assistant.py --mode journal --input journal/daily/2026-07-16-template.md
python app/ai_assistant.py --mode review --input reports/latest-trade.md
```
