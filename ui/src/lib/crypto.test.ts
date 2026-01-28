// @vitest-environment node
import { describe, expect, it } from "vitest";

import { computeApiKeyHash } from "./crypto";

describe("computeApiKeyHash", () => {
  it("matches python HMAC-SHA256 output", async () => {
    const hash = await computeApiKeyHash("rlm_key_local", "smoke-pepper");
    expect(hash).toBe(
      "6249402ece12673f89c027f9c718715d0105c1771f9ead10eb9a0772f5012c41",
    );
  });
});
