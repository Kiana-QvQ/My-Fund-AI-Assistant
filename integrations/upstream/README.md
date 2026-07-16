# Upstream Repositories

This directory contains local, shallow, read-only checkouts of third-party
projects used for reference or optional adapters.

| Project | Local path | Upstream |
|---|---|---|
| AI Berkshire | `ai-berkshire/` | https://github.com/xbtlin/ai-berkshire |
| Vibe-Trading | `vibe-trading/` | https://github.com/HKUDS/Vibe-Trading |

Do not edit these checkouts. Update them independently with `git pull` inside
the relevant directory. Project-specific code belongs in `app/`, `config/`,
`journal/`, `reports/`, or `integrations/adapters/`.
