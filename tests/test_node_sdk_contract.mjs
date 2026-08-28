import assert from "node:assert/strict";
import { ForgeCodeError, invoke } from "../sdk/node/index.mjs";

assert.equal(typeof ForgeCodeError, "function");
try {
  await invoke([], { executable: process.execPath, method: "bad method", timeoutMs: 1000 });
  assert.fail("expected RPC failure");
} catch (error) {
  assert.ok(error instanceof ForgeCodeError || error.code);
}
