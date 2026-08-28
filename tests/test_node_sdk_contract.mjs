import assert from "node:assert/strict";
import { ForgeCodeError, invoke, invokeStream } from "../sdk/node/index.mjs";

assert.equal(typeof ForgeCodeError, "function");
assert.throws(() => invoke([], { timeoutMs: NaN }), TypeError);
assert.throws(() => invoke(["x".repeat(1001)]), TypeError);
assert.throws(() => invoke([], { method: "doctor", params: "bad" }), TypeError);
assert.throws(() => invoke([], { method: "doctor", params: { value: "x".repeat(1_000_001) } }), TypeError);
assert.throws(() => invokeStream([], { maxItems: 0 }), TypeError);
assert.throws(() => invokeStream(["x".repeat(1001)]), TypeError);
try {
  await invoke([], { executable: process.execPath, method: "bad method", timeoutMs: 1000 });
  assert.fail("expected RPC failure");
} catch (error) {
  assert.ok(error instanceof ForgeCodeError || error.code);
}

try {
  await invoke([], { executable: "forgecode-command-that-does-not-exist", timeoutMs: 1000 });
  assert.fail("expected process failure");
} catch (error) {
  assert.ok(error instanceof ForgeCodeError);
  assert.equal(error.code, "process_error");
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
  assert.ok(error instanceof ForgeCodeError || error.code);
}
