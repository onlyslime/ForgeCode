import assert from "node:assert/strict";
import { ForgeCodeError, invoke, invokeStream } from "../sdk/node/index.mjs";

assert.equal(typeof ForgeCodeError, "function");
try {
  await invoke([], { executable: process.execPath, method: "bad method", timeoutMs: 1000 });
  assert.fail("expected RPC failure");
} catch (error) {
  assert.ok(error instanceof ForgeCodeError || error.code);
}

try {
  await invokeStream([], { executable: process.execPath, method: "bad method", maxItems: 1, timeoutMs: 1000 });
} catch (error) {
  assert.ok(error instanceof ForgeCodeError || error.code);
}

try {
  await invokeStream([], { method: "bad method", timeoutMs: 1000 });
  assert.fail("expected stream RPC failure");
} catch (error) {
  assert.ok(error instanceof ForgeCodeError);
  assert.equal(error.code, "invalid_request");
  assert.equal(error.envelope?.ok, false);
}
