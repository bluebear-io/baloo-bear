# Running Baloo on Databricks

This guide walks through configuring Baloo to run every agent through a Databricks workspace's AI Gateway instead of the direct Anthropic API. For the conceptual model (providers, tiers, short names) see [Model Configuration](models.md); for the full variable reference see [Configuration](../configuration.md#databricks).

## How Baloo talks to Databricks

Baloo runs the [PI](https://github.com/earendil-works/pi) coding agent as a sandboxed subprocess. Unlike Bedrock, **PI has no native Databricks provider**, so Baloo registers one itself:

1. On the first review with `AGENT_PROVIDER=databricks`, Baloo writes `~/.baloo/pi-databricks/models.json` describing a `databricks` provider pointed at `<workspace>/ai-gateway/anthropic`.
2. It sets `PI_CODING_AGENT_DIR` to that directory for the PI subprocess, which is how PI discovers the file.
3. PI speaks the Anthropic Messages dialect to the gateway, which serves Claude models from Unity Catalog.

The generated file contains **no secret**. Its `apiKey` field holds the *name* `DATABRICKS_TOKEN`, which PI resolves from the environment at request time.

Three settings in that file are load-bearing, each confirmed against a live workspace:

| Setting | Why |
|---|---|
| `authHeader: true` | The gateway requires `Authorization: Bearer` and returns 401 for the `x-api-key` header PI's built-in `anthropic` provider sends. Setting `ANTHROPIC_BASE_URL` does **not** work as a shortcut: PI passes the built-in provider's own base URL explicitly, so requests still reach api.anthropic.com. |
| `compat.supportsEagerToolInputStreaming: false` | The gateway's Anthropic translator rejects per-tool `eager_input_streaming` on the streaming+tools path every review uses. Without this flag requests **hang** instead of failing — surfacing as an `agent_error` with no detail rather than a clean error. |
| `system.ai.*` model IDs | Models are Unity Catalog model services. The older flat `databricks-claude-*` names now return `501 NOT_IMPLEMENTED: Use Unity Catalog model services (v3)`. |

`AGENT_PROVIDER` is global: selecting `databricks` routes the primary review, FP verification, thread agent, fidelity, documentation drift, and sync-scope agents through Databricks. There is no per-agent provider override.

## Prerequisites

- A Databricks workspace with the AI Gateway enabled.
- A workspace personal access token (PAT). A service-principal PAT is preferred over a personal one for a service deployment: a workspace admin creates the first token for the service principal, after which it can create its own.
- Access to the Claude model services you intend to use (see [Model availability](#model-availability)).

## Step 1 — Configure

```bash
AGENT_PROVIDER=databricks
AGENT_MODEL=sonnet
DATABRICKS_HOST=https://dbc-xxxxxxxx-xxxx.cloud.databricks.com
DATABRICKS_TOKEN=dapi...
```

`DATABRICKS_HOST` is the workspace URL. A trailing `/ai-gateway/anthropic` is accepted and stripped, so pasting the gateway URL from Databricks' own snippets works.

Both are **environment-only**. `DATABRICKS_HOST` is deliberately not runtime-mutable and shows as `env only` on the dashboard Settings page: if it could be changed from a web form, anyone with dashboard access could repoint the gateway at a host they control and Baloo would send `Authorization: Bearer $DATABRICKS_TOKEN` to it.

Setting `AGENT_PROVIDER=databricks` without `DATABRICKS_HOST` **fails at startup** with a message naming the missing variable, rather than starting and failing every review one PR at a time.

Selecting `databricks` from the dashboard provider selector is a separate path: the override is saved and the smoke test Baloo runs afterwards reports the same misconfiguration on the Settings page.

## Step 2 — Verify

Use **Test connection** on the dashboard Settings page after switching. It runs a short PI smoke call with the effective provider and model.

To check the gateway directly without Baloo:

```bash
curl -X POST "$DATABRICKS_HOST/ai-gateway/anthropic/v1/messages" \
  -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"model":"system.ai.claude-sonnet-4-6","max_tokens":16,
       "messages":[{"role":"user","content":"hi"}]}'
```

## Model availability

Short names resolve to these Unity Catalog model services:

| Tier | Short names | Model service |
|---|---|---|
| economy | `haiku`, `flash` | `system.ai.claude-haiku-4-5` |
| standard | `sonnet`, `standard` | `system.ai.claude-sonnet-4-6` |
| premium | `opus`, `premium` | `system.ai.claude-opus-4-6` |

Availability is **per workspace**. A model that exists but is not provisioned for you returns:

```
403 PERMISSION_DENIED: The endpoint is temporarily disabled due to a
Databricks-set rate limit of 0.
```

That is an entitlement problem, not a Baloo misconfiguration — enable the model service in the workspace or pick another tier. A model that does not exist at all returns `404 NOT_FOUND`.

To target a model outside the tier table, set `AGENT_MODEL` to its full ID:

```bash
AGENT_MODEL=system.ai.claude-opus-4-6
```

## Cost reporting

**Baloo reports `$0.00` for every Databricks review.** Token counts are accurate; only the dollar figure is unavailable.

Baloo derives cost either from its own price table (direct Anthropic only) or from the figure PI computes out of a model's declared prices. Databricks bills **DBUs** at a rate that depends on your contract and tier, so there is no correct per-token USD constant to hardcode — a wrong number that looks authoritative on the dashboard is worse than an obviously absent one.

If you want approximate figures, add a `cost` block (USD per million tokens) to each model in `build_models_config()` in `baloo/agent/databricks.py`; PI will then report a cost and Baloo will record it. Treat the result as an estimate.

## Sandbox notes

When `REPO_SANDBOX_MODE` is active, the agent runs under bwrap with a scrubbed environment:

- `DATABRICKS_TOKEN` and `PI_CODING_AGENT_DIR` are on the env allowlist; Baloo's own GitHub and database secrets are stripped.
- `~/.baloo/pi-databricks` is bind-mounted read-only, because the sandbox mounts a fresh tmpfs over `/tmp` and does not otherwise expose host paths. The generated config lives outside `/tmp` for exactly this reason.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `401 Credential was not sent or was of an unsupported type` | Token missing or unreadable. Confirm `DATABRICKS_TOKEN` is set and reaches the subprocess (sandbox allowlist). |
| `401 invalid x-api-key` | Requests are going to Anthropic, not the gateway — the `databricks` provider was not registered. Check that `PI_CODING_AGENT_DIR` is set and `models.json` exists. |
| `403 ... required scopes: unity-catalog` | The token is gateway-scoped. Harmless for reviews: Baloo does not list models, it only runs inference. |
| `501 NOT_IMPLEMENTED ... Use Unity Catalog model services` | A flat `databricks-claude-*` model ID. Use `system.ai.claude-*`. |
| Review hangs, then fails as `agent_error` with no detail | The `supportsEagerToolInputStreaming` compat flag is missing from `models.json`. Delete `~/.baloo/pi-databricks/models.json` so Baloo regenerates it. |
| `403 ... rate limit of 0` | The model service is not enabled for your workspace. See [Model availability](#model-availability). |
| App refuses to start: `AGENT_PROVIDER=databricks requires DATABRICKS_HOST` | The provider is selected but the host is unset. Set `DATABRICKS_HOST` in the environment and restart; it cannot be set from the dashboard. |
