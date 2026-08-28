import assert from "node:assert/strict";
import { ForgeCodeError, invoke, invokeStream, interactive } from "../sdk/node/index.mjs";

assert.equal(typeof ForgeCodeError, "function");
assert.throws(() => invoke([], { timeoutMs: NaN }), TypeError);
assert.throws(() => invoke(["x".repeat(1001)]), TypeError);
await assert.rejects(invoke([], { signal: {} }), TypeError);
{
  const controller = new AbortController(); controller.abort();
  await assert.rejects(invoke([], { executable: process.execPath, signal: controller.signal }), (error) => error.code === "cancelled");
  await assert.rejects(invokeStream([], { executable: process.execPath, signal: controller.signal }), (error) => error.code === "cancelled");
}
assert.throws(() => invoke([], { method: "doctor", params: "bad" }), TypeError);
assert.throws(() => invoke([], { method: "doctor", params: { value: "x".repeat(1_000_001) } }), TypeError);
assert.throws(() => invokeStream([], { maxItems: 0 }), TypeError);
assert.throws(() => invokeStream([], { maxStderrBytes: 0 }), TypeError);
assert.throws(() => interactive(".", { maxEvents: 0 }), TypeError);
{
  const session = interactive(".", { executable: process.execPath });
  session.close();
  assert.throws(() => session.send("hello"), (error) => error.code === "process_error");
}
{
  const session = interactive(".", { executable: process.execPath });
  let seen = null;
  session.on((event) => { seen = event; });
  session.process.stdout.emit("data", "not-json\n");
  assert.equal(seen?.code, "invalid_json");
  session.close();
}
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
