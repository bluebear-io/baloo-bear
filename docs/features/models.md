# Model Configuration

Baloo supports multiple LLM providers. **`AGENT_PROVIDER` is global** — every Baloo agent (primary review, FP verification, thread replies, fidelity, documentation drift, sync scope) uses that backend. Short names select a **model tier** on the provider; they are not a way to pick a different provider.

Use an explicit `provider/model` string only as an escape hatch (for example cross-provider fallback).

## Model tiers

| Short names | Tier | Max turns | Typical use |
|---|---|---|---|
| `flash`, `haiku` | Economy | 10 | FP verification, thread replies, simple PRs |
| `sonnet`, `standard`, `gemini-pro` | Standard | 20 | Default code reviews |
| `opus`, `premium`, `gemini-3.1-pro` | Premium | 30 | Complex / security-sensitive reviews |

Resolved model IDs depend on `AGENT_PROVIDER`:

| Provider | Economy | Standard | Premium |
|---|---|---|---|
| `anthropic` | `claude-haiku-4-5-20251001` | `claude-sonnet-5` | `claude-opus-5` |
| `google` | `gemini-3.5-flash-lite` | `gemini-3.6-flash` | `gemini-3.1-pro-preview` |
| `amazon-bedrock` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | `us.anthropic.claude-sonnet-5` | `us.anthropic.claude-opus-5` |
| `openai` | `gpt-5.6-luna` | `gpt-5.6-terra` | `gpt-5.6-sol` |

## Choosing a Model

- **Economy** (`flash`, `haiku`) — Good for simple PRs (docs, deps, configs). Fast and cheap. Also used internally for FP verification and thread replies.
- **Standard** (`sonnet`, `standard`, `gemini-pro`) — The default. Handles most code reviews well. Best cost/quality balance.
- **Premium** (`opus`, `premium`, `gemini-3.1-pro`) — Best for complex PRs with deep logic, security-sensitive code, or architectural changes.

## Amazon Bedrock

pi's provider token is `amazon-bedrock`. Point Baloo at it with:

```bash
AGENT_PROVIDER=amazon-bedrock
AGENT_MODEL=sonnet
# or a specific inference profile / ARN:
# AGENT_MODEL=us.anthropic.claude-sonnet-5
# AGENT_MODEL=amazon-bedrock/us.anthropic.claude-sonnet-5
AWS_REGION=us-east-1
```

With `AGENT_PROVIDER=amazon-bedrock`, short names such as `haiku` (FP/thread defaults) and `sonnet` (primary default) resolve to the Bedrock tier IDs above. Override with a bare Bedrock model ID or application inference profile ARN when your account uses different regional prefixes (`global.`, `eu.`, …).

Auth (pick one): IAM access keys (+ optional session token), `AWS_BEARER_TOKEN_BEDROCK`, `AWS_PROFILE`, IRSA (`AWS_WEB_IDENTITY_TOKEN_FILE` + `AWS_ROLE_ARN`), or ECS/EC2 instance roles. Baloo allowlists these AWS env vars into the sandboxed pi subprocess and bind-mounts IRSA/credential files when their paths are set. See [Configuration](../configuration.md#amazon-bedrock).

Cost estimation for Bedrock models currently falls back to whatever cost pi reports (Baloo's built-in pricing table is Anthropic-first-party only).

## Configuration

```bash
# Provider for all agents
AGENT_PROVIDER=anthropic

# Primary review tier (short name on that provider)
AGENT_MODEL=sonnet

# Or a full provider/model string (escape hatch)
AGENT_MODEL=anthropic/claude-sonnet-5

# Premium model for highest quality
AGENT_MODEL=opus
```

When `DATABASE_ENABLED=true`, `AGENT_PROVIDER`, `AGENT_MODEL`, `AGENT_FALLBACK_MODEL`, and `PI_THINKING_LEVEL` can also be changed at runtime from the dashboard Settings page without restarting. See [Runtime Overrides](../configuration.md#runtime-overrides-db).

## Automatic Fallback

If the primary model fails (rate limit, timeout, availability), Baloo automatically retries with a fallback model:

```bash
AGENT_FALLBACK_MODEL=google/gemini-2.5-flash
```

The default fallback uses an explicit `provider/model` string so it can stay on a different backend for availability. Set to empty to disable fallback.

When fallback is used, the review metadata includes:
- `fallback_used: true`
- `primary_model` — which model failed
- `primary_error` — why it failed

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
