# Dashboard

Baloo includes an optional review history dashboard backed by PostgreSQL. It provides visibility into review activity, costs, and findings across all repositories.

## What It Shows

- **Review history** — Every review with status, duration, model used, and cost
- **Findings** — Individual findings per review with severity, category, file, and line
- **Cost tracking** — Token usage and dollar cost per review and in aggregate
- **Fidelity scores** — When fidelity analysis is enabled
- **Settings** — Effective runtime configuration; allowlisted agent knobs can be edited when the database is enabled

The dashboard follows the viewer's light/dark preference and has a theme toggle in the header. All CSS and JavaScript are served from the application itself — no CDN — so it renders correctly offline and behind a firewall.

## Requirements

The dashboard requires:

1. **PostgreSQL** — For storing review data
2. **Database enabled** — `DATABASE_ENABLED=true`
3. **Dashboard enabled** — `DASHBOARD_ENABLED=true`
4. **Credentials** — `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD`

The default `docker-compose.yml` includes a PostgreSQL container, so no external database setup is needed for local use.

## Access

The dashboard is served at `/dashboard/` and protected by HTTP Basic Auth.

```
http://localhost:8000/dashboard/
```

## Settings Page

The dashboard includes a **Settings** page at `/dashboard/settings` showing the effective runtime configuration for this Baloo instance, grouped by category (GitHub App, Agent, Review Behavior, Repo Provisioning, etc.). A category rail jumps between groups, and the filter box narrows rows by variable name or description. Each row lists the environment variable, its current value, its default, and a description.

Allowlisted settings can be overridden at runtime when `DATABASE_ENABLED=true` — model selection, the agent feature toggles, and the tuning knobs (see [Runtime Overrides](../configuration.md#runtime-overrides-db) for the full list). Each renders the control its type calls for: a toggle for booleans, a bounded number input for integers, a select for enumerated values. Overridden rows show a `db` source badge. All other settings remain read-only and always come from environment variables.

Edits are batched: change several settings, and a save bar reports how many changed, with **Save** and **Discard**. Only changed fields are submitted, every field is validated against its type and bounds first, and a batch is all-or-nothing — if one field is invalid, nothing is written and the page reports which field failed.

`AGENT_PROVIDER` is a selector with labeled choices, including **Anthropic (direct API)**, **Amazon Bedrock**, and **Databricks AI Gateway**. It applies to **all** Baloo agents (primary, FP verification, thread, fidelity, docs, sync). Short names like `haiku` / `sonnet` / `opus` are model tiers on that provider, not separate backends — with Bedrock selected they resolve to Bedrock inference-profile IDs.

The page opens with a **Models in use** summary: each agent role (primary review, FP verification, thread agent, fidelity, documentation drift, sync scope) with its configured value and resolved `provider/model` (so defaults like `haiku` for FP/thread are visible without scrolling).

Use **Test connection** to run a one-shot PI smoke call against the effective provider/model (no tools, ~30s timeout). Saving or clearing `AGENT_PROVIDER` / `AGENT_MODEL` also auto-runs that smoke check and shows pass/fail on the page. Overrides are kept even if the smoke fails so you can inspect credentials and retry.

Secrets are never exposed: sensitive settings (API keys, private keys, passwords, webhook secrets) render as `Configured (redacted)` or `Not configured`, and `DATABASE_URL` is shown with its credentials stripped. Secrets cannot be stored as runtime overrides.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `DATABASE_ENABLED` | `false` | Enable PostgreSQL persistence |
| `DATABASE_URL` | — | PostgreSQL connection URL (auto-set in docker-compose) |
| `DASHBOARD_ENABLED` | `true` | Enable the dashboard UI (requires `DATABASE_ENABLED=true` + credentials to be useful) |
| `DASHBOARD_USERNAME` | — | Basic auth username |
| `DASHBOARD_PASSWORD` | — | Basic auth password |

## Without the Dashboard

If you don't need review history, leave `DATABASE_ENABLED=false`. Baloo works fine without a database — it just won't persist review data between restarts.
