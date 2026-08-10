# Model Configuration

Baloo supports multiple LLM providers. **`AGENT_PROVIDER` is global** — every Baloo agent (primary review, FP verification, thread replies, fidelity, documentation drift, sync scope) uses that backend. Short names select a **model tier** on the provider; they are not a way to pick a different provider.

Use an explicit `provider/model` string only when a specific provider model ID is required.

## Model tiers

| Short names | Tier | Max turns | Typical use |
|---|---|---|---|
| `flash`, `haiku` | Economy | 10 | FP verification, thread replies, simple PRs |
| `sonnet`, `standard`, `gemini-pro` | Standard | 20 | Default code reviews |
| `opus`, `premium`, `gemini-3.1-pro` | Premium | 30 | Complex / security-sensitive reviews |

Resolved model IDs depend on `AGENT_PROVIDER`:

| Provider | Economy | Standard | Premium |
|---|---|---|---|
| `anthropic` | `claude-haiku-4-5-20251001` | `claude-sonnet-4-6` | `claude-opus-4-6` |
| `google` | `gemini-3.5-flash-lite` | `gemini-3.6-flash` | `gemini-3.1-pro-preview` |
| `amazon-bedrock` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | `us.anthropic.claude-sonnet-4-6` | `us.anthropic.claude-opus-4-6-v1` |
| `openai` | `gpt-5.6-luna` | `gpt-5.6-terra` | `gpt-5.6-sol` |

Anthropic (and matching Bedrock Claude) tiers intentionally stay on Haiku 4.5 / Sonnet 4.6 / Opus 4.6 — the set Baloo already runs in production. Newer Claude generations can be opted into later via bare model IDs or `provider/model` strings.

## Choosing a Model

- **Economy** (`flash`, `haiku`) — Good for simple PRs (docs, deps, configs). Fast and cheap. Also used internally for FP verification and thread replies.
- **Standard** (`sonnet`, `standard`, `gemini-pro`) — The default. Handles most code reviews well. Best cost/quality balance.
- **Premium** (`opus`, `premium`, `gemini-3.1-pro`) — Best for complex PRs with deep logic, security-sensitive code, or architectural changes.

## Switching Providers

Provider selection is all-or-nothing: `AGENT_PROVIDER` applies to every agent, and short names are tiers on it. Moving an existing deployment (for example Anthropic → Bedrock) is normally a one-variable change.

**1. Make sure your model settings are tier short names.** Anything set to a bare provider-specific ID (`claude-sonnet-4-6`) or an explicit `provider/model` string is passed through as-is and will not translate. Short names (`sonnet`, `haiku`, `opus`) travel across providers; the defaults already use them.

**2. Change the provider.** Either set the environment variable and restart:

```bash
AGENT_PROVIDER=amazon-bedrock
```

…or, when `DATABASE_ENABLED=true`, change it on the dashboard Settings page with no restart. See [Runtime Overrides](../configuration.md#runtime-overrides-db).

**3. Add that provider's credentials** — see [API Keys](#api-keys).

**4. Verify.** The **Models in use** table on the Settings page should show the new provider for every role, and **Test connection** should pass.

## Amazon Bedrock

pi's provider token is `amazon-bedrock`. Point Baloo at it with:

```bash
AGENT_PROVIDER=amazon-bedrock
AGENT_MODEL=sonnet
# or a specific inference profile / ARN:
# AGENT_MODEL=us.anthropic.claude-sonnet-4-6
# AGENT_MODEL=amazon-bedrock/us.anthropic.claude-sonnet-4-6
AWS_REGION=us-east-1
```

With `AGENT_PROVIDER=amazon-bedrock`, short names such as `haiku` (FP/thread defaults) and `sonnet` (primary default) resolve to the Bedrock tier IDs above. Override with a bare Bedrock model ID or application inference profile ARN when your account uses different regional prefixes (`global.`, `eu.`, …).

Auth (pick one): IAM access keys (+ optional session token), `AWS_BEARER_TOKEN_BEDROCK`, `AWS_PROFILE`, IRSA (`AWS_WEB_IDENTITY_TOKEN_FILE` + `AWS_ROLE_ARN`), or ECS/EC2 instance roles. Baloo allowlists these AWS env vars into the sandboxed pi subprocess and bind-mounts IRSA/credential files when their paths are set. See [Configuration](../configuration.md#amazon-bedrock).

Cost estimation for Bedrock models uses the cost pi reports (Baloo's built-in pricing table is Anthropic-first-party only).

## Configuration

```bash
# Provider for all agents
AGENT_PROVIDER=anthropic

# Primary review tier (short name on that provider)
AGENT_MODEL=sonnet

# Or a full provider/model string (escape hatch)
AGENT_MODEL=anthropic/claude-sonnet-4-6

# Premium model for highest quality
AGENT_MODEL=opus
```

When `DATABASE_ENABLED=true`, `AGENT_PROVIDER`, `AGENT_MODEL`, and `PI_THINKING_LEVEL` can also be changed at runtime from the dashboard Settings page without restarting. See [Runtime Overrides](../configuration.md#runtime-overrides-db).

## API Keys

Each provider needs its own credentials:

| Provider | Environment Variable / Auth |
|---|---|
| Anthropic | `ANTHROPIC_API_KEY` |
| Google | `GEMINI_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Amazon Bedrock | AWS credentials / IRSA / bearer token (see [Amazon Bedrock](#amazon-bedrock)) |

## Thinking Level

Controls the depth of reasoning the model uses:

```bash
PI_THINKING_LEVEL=medium  # off, minimal, low, medium, high
```

Higher thinking = better analysis but slower and more expensive. `medium` is the default and recommended for most use cases.

## Cost Estimates

Approximate cost per review (typical 5-file PR):

| Model | Cost per Review |
|---|---|
| `flash` | ~$0.005 |
| `haiku` | ~$0.01 |
| `sonnet` | ~$0.03–0.08 |
| `opus` | ~$0.15–0.40 |
