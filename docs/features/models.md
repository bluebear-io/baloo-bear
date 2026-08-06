# Model Configuration

Baloo supports multiple LLM providers and models. You can use short names for convenience or specify full `provider/model` strings.

## Model Registry

| Short Name | Provider | Model ID | Max Turns | Tier |
|---|---|---|---|---|
| `flash` | Google | gemini-2.5-flash | 10 | Economy |
| `haiku` | Anthropic | claude-haiku-4-5 | 10 | Economy |
| `sonnet` | Anthropic | claude-sonnet-4-6 | 20 | Standard |
| `standard` | Anthropic | claude-sonnet-4-6 | 20 | Standard (alias for `sonnet`) |
| `gemini-pro` | Google | gemini-2.5-pro | 20 | Standard |
| `opus` | Anthropic | claude-opus-4-6 | 30 | Premium |
| `premium` | Google | gemini-3.1-pro-preview | 30 | Premium |
| `gemini-3.1-pro` | Google | gemini-3.1-pro-preview | 30 | Premium (alias for `premium`) |

## Choosing a Model

- **Economy** (`flash`, `haiku`) — Good for simple PRs (docs, deps, configs). Fast and cheap. Also used internally for FP verification.
- **Standard** (`sonnet`, `standard`, `gemini-pro`) — The default. Handles most code reviews well. Best cost/quality balance.
- **Premium** (`opus`, `premium`, `gemini-3.1-pro`) — Best for complex PRs with deep logic, security-sensitive code, or architectural changes.

## Amazon Bedrock

pi natively supports AWS Bedrock under the provider token `amazon-bedrock`. Point Baloo at it with:

```bash
AGENT_PROVIDER=amazon-bedrock
AGENT_MODEL=us.anthropic.claude-sonnet-4-20250514-v1:0
# equivalent: AGENT_MODEL=amazon-bedrock/us.anthropic.claude-sonnet-4-20250514-v1:0
AWS_REGION=us-east-1
```

Model IDs are Bedrock inference-profile style (e.g. `us.anthropic.claude-sonnet-4-20250514-v1:0`) or application inference profile ARNs. There are no Baloo short-name aliases for Bedrock — IDs vary by region and account.

Auth (pick one): IAM access keys (+ optional session token), `AWS_BEARER_TOKEN_BEDROCK`, `AWS_PROFILE`, IRSA (`AWS_WEB_IDENTITY_TOKEN_FILE` + `AWS_ROLE_ARN`), or ECS/EC2 instance roles. Baloo allowlists these AWS env vars into the sandboxed pi subprocess and bind-mounts IRSA/credential files when their paths are set. See [Configuration](../configuration.md#amazon-bedrock).

Cost estimation for Bedrock models currently falls back to whatever cost pi reports (Baloo's built-in pricing table is Anthropic-first-party only).

## Configuration

```bash
# Use a short name
AGENT_MODEL=sonnet

# Or a full provider/model string
AGENT_MODEL=anthropic/claude-sonnet-4-6

# Premium model for highest quality
AGENT_MODEL=opus
```

When `DATABASE_ENABLED=true`, `AGENT_MODEL`, `AGENT_FALLBACK_MODEL`, and `PI_THINKING_LEVEL` can also be changed at runtime from the dashboard Settings page without restarting. See [Runtime Overrides](../configuration.md#runtime-overrides-db).

## Automatic Fallback

If the primary model fails (rate limit, timeout, availability), Baloo automatically retries with a fallback model:

```bash
AGENT_FALLBACK_MODEL=google/gemini-2.5-flash
```

The fallback uses a different provider to maximize availability. Set to empty to disable fallback.

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
| `gemini-pro` | ~$0.05–0.15 |
| `opus` | ~$0.15–0.40 |

Actual costs depend on PR size, number of agent turns, and thinking level.
