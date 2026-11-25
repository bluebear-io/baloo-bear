import assert from "node:assert/strict";
import { test } from "node:test";

// Node 26 strips TypeScript types when importing local extension files.
const { default: register } = await import("./bedrock-service-tier.ts");

function run(tier, provider, payload) {
  const previous = process.env.BEDROCK_SERVICE_TIER;
  try {
    if (tier === undefined) delete process.env.BEDROCK_SERVICE_TIER;
    else process.env.BEDROCK_SERVICE_TIER = tier;
    let handler;
    register({ on(event, callback) { assert.equal(event, "before_provider_request"); handler = callback; } });
    return handler?.({ payload }, { model: { provider } });
  } finally {
    if (previous === undefined) delete process.env.BEDROCK_SERVICE_TIER;
    else process.env.BEDROCK_SERVICE_TIER = previous;
  }
}

const request = { modelId: "model", messages: [], inferenceConfig: { maxTokens: 42 } };

for (const tier of ["flex", "priority"]) {
  test(`${tier} adds a service tier to Bedrock without mutating the original`, () => {
    assert.deepEqual(run(tier, "amazon-bedrock", request), {
      ...request, serviceTier: { type: tier },
    });
    assert.equal(request.serviceTier, undefined);
  });
}

test("default leaves the payload untouched", () => {
  assert.equal(run("default", "amazon-bedrock", request), undefined);
});

test("unset setting leaves the payload untouched", () => {
  assert.equal(run(undefined, "amazon-bedrock", request), undefined);
});

test("other providers are not affected", () => {
  assert.equal(run("flex", "anthropic", request), undefined);
});

test("non-Converse payloads are ignored", () => {
  assert.equal(run("flex", "amazon-bedrock", { model: "x" }), undefined);
});
