# Running Baloo on OpenRouter (ZDR only)

This guide covers running every Baloo agent on open-weight models (GLM, Kimi, and anything else OpenRouter serves) through [OpenRouter](https://openrouter.ai), restricted to endpoints with a **Zero Data Retention** policy. For the conceptual model (providers, tiers, short names) see [Model Configuration](models.md); for the variable reference see [Configuration](../configuration.md#openrouter).

## How Baloo enforces ZDR

OpenRouter is a built-in PI provider, so authentication and the model catalog come from PI. What PI cannot take on the command line is OpenRouter's [provider routing](https://openrouter.ai/docs/guides/routing/provider-selection) preferences. Baloo therefore writes `~/.baloo/pi-openrouter/models.json` and points the PI subprocess at it with `PI_CODING_AGENT_DIR`. The file sets, for every OpenRouter model:

```json
{"provider": {"zdr": true, "data_collection": "deny", "order": [...], "allow_fallbacks": ...}}
```

- `zdr: true` makes OpenRouter route only to endpoints that do not retain prompts. If no ZDR endpoint can serve a request, it fails rather than falling back to one that does.
- `data_collection: "deny"` excludes any endpoint that stores or trains on prompts.

Both are hardcoded and cannot be turned off from settings or the dashboard. The file also caps each configured model's output at 65,536 tokens (see [Troubleshooting](#troubleshooting)). It holds no secret.

**Turn on ZDR at the account level too.** In OpenRouter, open *Settings → Privacy* and enable the ZDR-only option for the key Baloo uses. If the generated file ever failed to reach PI, PI would still run OpenRouter as a built-in provider but without the per-request constraint. The account setting closes that gap.

## Default models

| Tier | Short names | Model |
|---|---|---|
| Economy | `flash`, `haiku` | `z-ai/glm-5.3-flash` |
| Standard | `sonnet`, `standard` | `z-ai/glm-5.3` |
| Premium | `opus`, `premium` | `moonshotai/kimi-k2.7-code` |

Standard and Premium reviews may run up to 50 turns (30 on other providers), since turns on these models are cheap. Any OpenRouter model ID in PI's catalog also works, with or without the `openrouter/` prefix (`AGENT_MODEL=moonshotai/kimi-k2.6`). Every model inherits the ZDR routing. Check that a model has ZDR endpoints in OpenRouter's [ZDR endpoint list](https://openrouter.ai/api/v1/endpoints/zdr) before selecting it; otherwise every request fails with no available provider.

## Step 1 — Configure

```bash
AGENT_PROVIDER=openrouter
AGENT_MODEL=sonnet
OPENROUTER_API_KEY=sk-or-...
```

## Step 2 — Pin providers (recommended)

The same model is served by many upstream providers at prices that differ by 3× or more, and some serve quantized weights or handle tool calls less reliably. Pinning matters for cost too. Reviews are dominated by prompt-cache reads, and the cache only holds while a review stays on one upstream (PI sends an `x-session-id` header so OpenRouter keeps a session sticky). A fallback mid-review re-bills the whole context as fresh input.

```bash
OPENROUTER_PROVIDER_ORDER=novita,deepinfra
OPENROUTER_ALLOW_FALLBACKS=false
```

Provider slugs are OpenRouter's (lowercase). A provider in the list that does not serve the selected model is skipped. With fallbacks disabled, a request fails if none of the listed providers can serve it, so list at least one provider for each model you use. Both settings are dashboard-editable, which makes it easy to compare providers during a trial.

## Step 3 — Verify

Use **Test connection** on the dashboard Settings page, then trigger a review with `@baloo review` on a PR. In OpenRouter's *Activity* page, each request shows the upstream provider it was routed to.

## Cost reporting

PI computes cost from its own catalog prices, not from what OpenRouter actually bills for the provider a request was routed to. The dashboard's per-review cost is therefore an estimate. Use OpenRouter's *Activity* export for real spend, especially when comparing providers.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `No endpoints found matching your data policy` | The model has no ZDR endpoint, or none among `OPENROUTER_PROVIDER_ORDER` with fallbacks disabled. |
| `No endpoints found … Filter by Context Length removed …` | The request asked for more output tokens than the remaining endpoints allow. pi requests the model's catalog maximum (943K for `z-ai/glm-5.3`), so Baloo caps every model it is configured with at 65,536 output tokens. A model passed only on the command line (e.g. a dry run's `--model`) is not in settings and is not capped. |
| `401` from OpenRouter | `OPENROUTER_API_KEY` is missing or wrong. In the sandbox only allowlisted env vars reach PI; the key is on that list. |
| Reviews cost much more than expected | Check the cache-read share in OpenRouter's Activity. A low share usually means requests are hopping providers; pin them and disable fallbacks. |
