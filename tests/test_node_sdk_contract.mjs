import assert from "node:assert/strict";
import { ForgeCodeError, invoke } from "../sdk/node/index.mjs";

assert.equal(typeof ForgeCodeError, "function");
try {
  await invoke([], { executable: process.execPath, method: "bad method", timeoutMs: 1000 });
  assert.fail("expected RPC failure");
} catch (error) {
  assert.ok(error instanceof ForgeCodeError || error.code);
}

try {
  await (await import("../sdk/node/index.mjs")).invokeStream([], { executable: process.execPath, method: "bad method", maxItems: 1, timeoutMs: 1000 });
} catch (error) {
  assert.ok(error instanceof ForgeCodeError || error.code);
}
