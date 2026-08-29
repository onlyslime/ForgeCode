import assert from "node:assert/strict";
import { ForgeCodeError, invoke, invokeStream, interactive, sessionApproval, sessionControl, sessionList, sessionOpen, sessionTree, trust } from "../sdk/node/index.mjs";

assert.equal(typeof ForgeCodeError, "function");
assert.equal(typeof sessionList, "function");
assert.equal(typeof sessionTree, "function");
assert.equal(typeof sessionOpen, "function");
assert.equal(typeof sessionApproval, "function");
assert.equal(typeof trust, "function");
assert.throws(() => invoke([], { timeoutMs: NaN }), TypeError);
assert.throws(() => invoke(["x".repeat(1001)]), TypeError);
assert.throws(() => invoke([], { signal: {} }), TypeError);
assert.throws(() => trust("status", { workspace: "bad\npath" }), TypeError);
assert.throws(() => trust("status", { workspace: "x".repeat(1001) }), TypeError);
assert.throws(() => trust("delete"), TypeError);
assert.throws(() => sessionApproval("session", "yes"), TypeError);
assert.throws(() => sessionApproval("bad\nsession", true), TypeError);
assert.throws(() => sessionControl("session", "explode"), TypeError);
{
  const controller = new AbortController(); controller.abort();
  await assert.rejects(invoke([], { executable: process.execPath, signal: controller.signal }), (error) => error.code === "cancelled");
  await assert.rejects(invokeStream([], { executable: process.execPath, signal: controller.signal }), (error) => error.code === "cancelled");
}
{
  const controller = new AbortController();
  const request = invoke([], { executable: process.execPath, method: "doctor", signal: controller.signal });
  controller.abort();
  await assert.rejects(request, (error) => error.code === "cancelled");
  controller.abort();
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
  const session = interactive(".", { executable: "forgecode-missing-interactive" });
  const event = await new Promise((resolve) => { const timer = setTimeout(() => resolve(null), 1000); session.on((value) => { if (value.code === "process_error") { clearTimeout(timer); resolve(value); } }); });
  assert.equal(event?.code, "process_error");
  session.close();
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
