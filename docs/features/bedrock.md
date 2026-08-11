# Running Baloo on Amazon Bedrock

This guide walks through configuring Baloo to run every agent through Amazon Bedrock instead of the direct Anthropic API. For the conceptual model (providers, tiers, short names) see [Model Configuration](models.md); for the full variable reference see [Configuration](../configuration.md#amazon-bedrock).

## How Baloo talks to Bedrock

Baloo runs the [PI](https://github.com/mariozechner/pi-coding-agent) coding agent as a sandboxed subprocess. PI has a native `amazon-bedrock` provider that uses the AWS SDK, so Baloo does not call Bedrock directly — it selects the provider and passes AWS credentials through to that subprocess.

Two consequences follow from the sandbox:

- Only an allowlist of `AWS_*` environment variables is forwarded into the sandbox; everything else (including Baloo's own GitHub and database secrets) is stripped so a prompt-injected agent cannot read them.
- The sandbox has its own filesystem view. Credential **files** are only visible if Baloo bind-mounts them, which it does for the file paths named in `AWS_WEB_IDENTITY_TOKEN_FILE`, `AWS_SHARED_CREDENTIALS_FILE`, `AWS_CONFIG_FILE`, and `AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE`.

`AGENT_PROVIDER` is global: selecting `amazon-bedrock` routes the primary review, FP verification, thread agent, fidelity, documentation drift, and sync-scope agents through Bedrock. There is no per-agent provider override.

## Prerequisites

- Bedrock model access enabled in your AWS account for the Claude models you intend to use (request access in the Bedrock console).
- One of the authentication methods below.
- A region where those models (or their inference profiles) are available.

## Step 1 — Choose an authentication method

Pick exactly one. They are listed roughly in order of operational preference for a service deployment.

### EC2 instance role or ECS task role (recommended on AWS)

If Baloo runs on EC2 or ECS/Fargate with an attached role that has Bedrock permissions, you do **not** need to set any secret. The AWS SDK inside PI resolves credentials from instance metadata or the ECS credential endpoint over the shared network.

```bash
AGENT_PROVIDER=amazon-bedrock
AGENT_MODEL=sonnet
AWS_REGION=us-east-1
```

ECS task roles set `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI` (or `_FULL_URI`) automatically; those variables are on the allowlist and pass through untouched.

### IRSA (recommended on EKS)

For Kubernetes with IAM Roles for Service Accounts, the pod projects a web-identity token file and sets two variables. Baloo bind-mounts the token file into the sandbox automatically.

```bash
AGENT_PROVIDER=amazon-bedrock
AGENT_MODEL=sonnet
AWS_REGION=us-east-1
AWS_WEB_IDENTITY_TOKEN_FILE=/var/run/secrets/eks.amazonaws.com/serviceaccount/token
AWS_ROLE_ARN=arn:aws:iam::<account-id>:role/<baloo-role>
```

### Bearer token

Bedrock supports a bearer token for API-key-style auth. It is a single environment variable, which makes it the simplest option outside AWS.

```bash
AGENT_PROVIDER=amazon-bedrock
AGENT_MODEL=sonnet
AWS_REGION=us-east-1
AWS_BEARER_TOKEN_BEDROCK=<token>
```

Treat this token like any other secret: inject it from your secret manager, not from a checked-in file.

### Static IAM keys

```bash
AGENT_PROVIDER=amazon-bedrock
AGENT_MODEL=sonnet
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
# AWS_SESSION_TOKEN=...   # only for temporary credentials
```

### Shared profile (`AWS_PROFILE`)

!!! warning "Profiles need an explicit, bind-mounted credentials file"
    `AWS_PROFILE` alone is **not** sufficient when reviews run in the bwrap sandbox. The sandbox does not expose your home directory, so the default `~/.aws/credentials` and `~/.aws/config` are invisible to the agent even though the dashboard **Test connection** probe (which is not sandboxed) may still pass.

    To use a profile, point the SDK at explicit files and let Baloo bind-mount them:

    ```bash
    AGENT_PROVIDER=amazon-bedrock
    AGENT_MODEL=sonnet
    AWS_REGION=us-east-1
    AWS_PROFILE=bedrock
    AWS_SHARED_CREDENTIALS_FILE=/etc/baloo/aws/credentials
    AWS_CONFIG_FILE=/etc/baloo/aws/config
    ```

    Prefer a role or bearer token where possible; they avoid this footgun entirely.

## Step 2 — Pick a model

Leave `AGENT_MODEL` as a tier short name and let Baloo resolve it to the Bedrock inference-profile ID:

| Short name | Resolves to (Bedrock) |
|---|---|
| `haiku` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `sonnet` | `us.anthropic.claude-sonnet-4-6` |
| `opus` | `us.anthropic.claude-opus-4-6-v1` |

These use the `us.` cross-region inference prefix. If your account or data-residency policy requires a different routing prefix (`eu.`, `apac.`, `global.`) or an application inference profile ARN, set `AGENT_MODEL` to that full ID instead of a short name — a value containing a specific model ID is passed through unchanged:

```bash
AGENT_MODEL=eu.anthropic.claude-sonnet-4-6
# or an ARN
AGENT_MODEL=arn:aws:bedrock:eu-central-1:<account-id>:application-inference-profile/<id>
```

## Step 3 — Apply the configuration

=== "Environment / restart"

    Add the variables to your `.env` (or deployment secret store) and restart Baloo. This is the only option before the service is running.

=== "Dashboard (no restart)"

    When `DATABASE_ENABLED=true`, an operator can change `AGENT_PROVIDER` and `AGENT_MODEL` on the **Settings** page without a restart. Credentials are **not** editable here — they are read-only, redacted, and must already be present in the environment. So the usual flow is: add AWS credentials to the environment once, then flip the provider on the dashboard.

## Step 4 — Verify

1. Open **Settings** in the dashboard and check the **Models in use** table. Every role should show `amazon-bedrock/...`.
2. Click **Test connection** to run a one-shot Bedrock call (no tools, ~30s timeout).
3. Open a small test PR and confirm the posted review's model is a Bedrock ID, then check `aws logs tail` (or your log sink) for `spawning PI process (model=us.anthropic.…)` lines across the primary and FP-verifier agents.

!!! note "Test connection is not sandboxed"
    The smoke test deliberately runs without the repo sandbox, so it validates credentials and endpoint wiring but **not** the sandbox's view of credential files. If **Test connection** passes but real reviews fail with auth errors, suspect a file-visibility issue — see the `AWS_PROFILE` warning above.

## Optional Bedrock tuning

These map directly to PI / AWS SDK behavior and are only needed for proxies or application inference profiles:

| Variable | Use |
|---|---|
| `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` | Route through a Bedrock proxy endpoint |
| `AWS_BEDROCK_FORCE_CACHE` | Force prompt caching for application inference profile ARNs |
| `AWS_BEDROCK_SKIP_AUTH` | Skip auth for an unauthenticated proxy |
| `AWS_BEDROCK_FORCE_HTTP1` | Force HTTP/1.1 for proxies that need it |

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Every review fails auth, but **Test connection** passes | Credentials live in a file the sandbox can't see (default `~/.aws`). Use a role/bearer token, or set `AWS_SHARED_CREDENTIALS_FILE` / `AWS_CONFIG_FILE`. |
| `AccessDeniedException` for a model ID | Model access not enabled in the account/region, or the IAM policy lacks `bedrock:InvokeModel` on that inference profile. |
| Model-not-found / validation error | The `us.` prefix isn't valid in your region. Set `AGENT_MODEL` to the correct regional profile or ARN. |
| Dashboard shows `anthropic/...` after switching | The provider change didn't take effect — a hardcoded `AGENT_MODEL`/`AGENT_PROVIDER` in the Compose `environment:` block overrides `env_file`. Check **Models in use**. |

## Cost note

Baloo's built-in pricing table is Anthropic first-party only, so for Bedrock models the per-review cost shown in the dashboard falls back to whatever cost PI reports.
