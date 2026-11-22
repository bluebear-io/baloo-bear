import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/** Apply the configured service tier only to Bedrock ConverseStream requests. */
export default function (pi: ExtensionAPI): void {
  const tier = process.env.BEDROCK_SERVICE_TIER;
  if (tier !== "flex" && tier !== "priority") return;

  pi.on("before_provider_request", (event, ctx) => {
    if (ctx.model?.provider !== "amazon-bedrock") return;
    const payload = event.payload;
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) return;
    if (!("modelId" in payload) || !("messages" in payload)) return;
    return { ...payload, serviceTier: { type: tier } };
  });
}
