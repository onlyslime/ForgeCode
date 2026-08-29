/** Minimal Node SDK for ForgeCode's JSONL RPC/CLI envelope. */
import { spawn } from "node:child_process";

function boundedNumber(value, name, { min = 1, integer = false } = {}) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || (integer && !Number.isInteger(value))) throw new TypeError(`${name} must be a finite ${integer ? "integer" : "number"} >= ${min}`);
  return value;
}
function validateArgv(argv) {
  if (!Array.isArray(argv) || argv.length > 128 || argv.some((item) => typeof item !== "string" || item.length > 1000)) throw new TypeError("argv must contain at most 128 bounded string arguments");
}
function validateParams(params) {
  if (params === null || typeof params !== "object" || Array.isArray(params)) throw new TypeError("params must be an object");
  let encoded;
  try { encoded = JSON.stringify(params); } catch { throw new TypeError("params must be JSON-serializable"); }
  if (Buffer.byteLength(encoded, "utf8") > 1_000_000) throw new TypeError("params exceed 1 MiB");
}
function validateWorkspace(workspace) {
  if (workspace !== undefined && (typeof workspace !== "string" || !workspace || workspace.length > 1000 || /[\r\n]/.test(workspace))) throw new TypeError("workspace must be bounded newline-safe text");
}
function validateTrustAction(action) {
  if (action !== "status" && action !== "grant" && action !== "revoke") throw new TypeError("trust action must be status, grant, or revoke");
}
function validateSession(session) {
  if (typeof session !== "string" || !session || session.length > 512 || /[\r\n]/.test(session)) throw new TypeError("session must be bounded newline-safe text");
}

export class ForgeCodeError extends Error {
  constructor(message, { code = "sdk_error", envelope = null, exitCode = null } = {}) {
    super(message); this.name = "ForgeCodeError"; this.code = code; this.envelope = envelope; this.exitCode = exitCode;
  }
}

export function invoke(argv = [], { cwd, executable = "forgecode", method, params = {}, id, timeoutMs = 30000, maxOutputBytes = 2_000_000, signal } = {}) {
  validateArgv(argv);
  if (signal !== undefined && (typeof signal.addEventListener !== "function" || typeof signal.removeEventListener !== "function")) throw new TypeError("signal must be an AbortSignal");
  if (method !== undefined) validateParams(params);
  boundedNumber(timeoutMs, "timeoutMs"); boundedNumber(maxOutputBytes, "maxOutputBytes", { integer: true });
  return new Promise((resolve, reject) => {
    const rpc = method !== undefined;
    const child = spawn(executable, rpc ? ["rpc"] : [...argv, "--jsonl"], { cwd, stdio: ["pipe", "pipe", "pipe"] });
    let out = "";
    let err = "";
    let settled = false;
    const onAbort = () => { if (!settled) { child.kill(); settled = true; clearTimeout(timer); signal?.removeEventListener?.("abort", onAbort); reject(new ForgeCodeError("request cancelled", { code: "cancelled" })); } };
    const timer = setTimeout(() => { if (!settled) { child.kill(); settled = true; signal?.removeEventListener?.("abort", onAbort); reject(new ForgeCodeError("request timed out", { code: "timeout" })); } }, Math.max(1, timeoutMs));
    if (signal !== undefined) { if (signal.aborted) return onAbort(); signal.addEventListener("abort", onAbort, { once: true }); }
    if (rpc) child.stdin.write(JSON.stringify({ argv: [], method, params, ...(id === undefined ? {} : { id }) }) + "\n");
    child.stdin.end();
    child.stdout.on("data", (chunk) => { out += chunk; if (Buffer.byteLength(out) > maxOutputBytes && !settled) { child.kill(); settled = true; clearTimeout(timer); reject(new ForgeCodeError("response exceeds output limit", { code: "output_limit" })); } });
    child.stderr.on("data", (chunk) => { err += chunk; });
    child.on("error", (error) => { if (!settled) { settled = true; clearTimeout(timer); reject(new ForgeCodeError(error.message || "process failed", { code: "process_error" })); } });
    child.on("close", (code) => {
      if (settled) return;
      settled = true; clearTimeout(timer); signal?.removeEventListener?.("abort", onAbort);
      const line = out.trim().split(/\r?\n/).filter(Boolean).pop();
      if (!line) return reject(new ForgeCodeError(err.trim() || `forgecode exited ${code}`, { code: "empty_response", exitCode: code }));
      try {
        const envelope = JSON.parse(line);
        if (envelope.ok === false) return reject(new ForgeCodeError(envelope.error?.message || "ForgeCode request failed", { code: envelope.error?.code || "request_failed", envelope, exitCode: code }));
        resolve({ ...envelope, process_exit_code: code });
      } catch (error) { reject(new ForgeCodeError(error.message, { code: "invalid_json", exitCode: code })); }
    });
  });
}

export function invokeStream(argv = [], options = {}) {
    validateArgv(argv);
    if (options.signal !== undefined && (typeof options.signal.addEventListener !== "function" || typeof options.signal.removeEventListener !== "function")) throw new TypeError("signal must be an AbortSignal");
    if (options.method !== undefined) validateParams(options.params ?? {});
    boundedNumber(options.timeoutMs ?? 30000, "timeoutMs");
    boundedNumber(options.maxOutputBytes ?? 2_000_000, "maxOutputBytes", { integer: true });
    boundedNumber(options.maxItems ?? 1024, "maxItems", { integer: true });
    boundedNumber(options.maxStderrBytes ?? 256_000, "maxStderrBytes", { integer: true });
  return new Promise((resolve, reject) => {
    const rpc = options.method !== undefined;
    const child = spawn(options.executable ?? "forgecode", rpc ? ["rpc"] : [...argv, "--jsonl"], { cwd: options.cwd, stdio: ["pipe", "pipe", "pipe"] });
    if (rpc) { child.stdin.write(JSON.stringify({ argv: [], method: options.method, params: options.params ?? {}, ...(options.id === undefined ? {} : { id: options.id }) }) + "\n"); child.stdin.end(); }
    let buffer = ""; const events = []; let err = ""; let bytes = 0; let settled = false;
    const onAbort = () => { if (!settled) { child.kill(); settled = true; clearTimeout(timer); options.signal?.removeEventListener?.("abort", onAbort); reject(new ForgeCodeError("request cancelled", { code: "cancelled" })); } };
    const timer = setTimeout(() => { if (!settled) { child.kill(); settled = true; options.signal?.removeEventListener?.("abort", onAbort); reject(new ForgeCodeError("request timed out", { code: "timeout" })); } }, Math.max(1, options.timeoutMs ?? 30000));
    if (options.signal !== undefined) { if (options.signal.aborted) return onAbort(); options.signal.addEventListener("abort", onAbort, { once: true }); }
    child.stdout.on("data", (chunk) => {
      bytes += chunk.byteLength;
      if (bytes > (options.maxOutputBytes ?? 2_000_000) && !settled) { child.kill(); settled = true; clearTimeout(timer); reject(new ForgeCodeError("response exceeds output limit", { code: "output_limit" })); return; }
      buffer += chunk;
      const lines = buffer.split(/\r?\n/); buffer = lines.pop();
      for (const line of lines) if (line.trim()) {
        if (events.length >= (options.maxItems ?? 1024)) { child.kill(); settled = true; clearTimeout(timer); reject(new ForgeCodeError("response exceeds item limit", { code: "output_limit" })); return; }
        try {
          const envelope = JSON.parse(line);
          if (envelope?.ok === false) { child.kill(); settled = true; clearTimeout(timer); reject(new ForgeCodeError(envelope.error?.message || "ForgeCode request failed", { code: envelope.error?.code || "request_failed", envelope })); return; }
          events.push(envelope);
        }
        catch (error) { child.kill(); settled = true; clearTimeout(timer); reject(new ForgeCodeError(error.message, { code: "invalid_json" })); return; }
      }
    });
    child.stderr.on("data", (chunk) => { err += chunk; if (Buffer.byteLength(err, "utf8") > (options.maxStderrBytes ?? 256_000)) err = err.slice(-((options.maxStderrBytes ?? 256_000) / 2)); });
    child.on("error", (error) => { if (!settled) { settled = true; clearTimeout(timer); reject(new ForgeCodeError(error.message || "process failed", { code: "process_error" })); } });
    child.on("close", (code) => {
      if (settled) return; settled = true; clearTimeout(timer); options.signal?.removeEventListener?.("abort", onAbort);
      if (buffer.trim()) {
        try {
          const envelope = JSON.parse(buffer);
          if (envelope?.ok === false) return reject(new ForgeCodeError(envelope.error?.message || "ForgeCode request failed", { code: envelope.error?.code || "request_failed", envelope, exitCode: code }));
          events.push(envelope);
        } catch (error) { return reject(new ForgeCodeError(error.message, { code: "invalid_json", exitCode: code })); }
      }
      if (!events.length) return reject(new ForgeCodeError(err.trim() || `forgecode exited ${code}`, { code: "empty_response", exitCode: code }));
      resolve({ events, process_exit_code: code });
    });
  });
}

export const trust = (action = "status", { workspace, ...options } = {}) => invoke([
  ...(validateTrustAction(action), validateWorkspace(workspace), workspace === undefined ? [] : ["--workspace", workspace]),
  "trust", action,
], options);
export const login = ({ profile, provider, apiKeyEnv, ...options } = {}) => invoke([], {
  ...options,
  method: "login",
  params: {
    ...(profile === undefined ? {} : { profile }),
    ...(provider === undefined ? {} : { provider }),
    ...(apiKeyEnv === undefined ? {} : { api_key_env: apiKeyEnv }),
  },
});
export const method = (name, options = {}) => invoke([], { ...options, method: name });
export const run = (prompt, options = {}) => method("run", { ...options, params: { ...(options.params ?? {}), prompt } });
export const sessionInspect = (session, { workspace, ...options } = {}) => method("session.inspect", { ...options, params: { ...(options.params ?? {}), session, ...(workspace === undefined ? {} : { workspace }) } });
export const sessionTree = ({ workspace, limit, ...options } = {}) => method("session.tree", {
  ...options,
  params: {
    ...(options.params ?? {}),
    ...(workspace === undefined ? {} : { workspace }),
    ...(limit === undefined ? {} : { limit }),
  },
});
export const sessionList = ({ workspace, state, limit, ...options } = {}) => method("session.list", {
  ...options,
  params: {
    ...(options.params ?? {}),
    ...(workspace === undefined ? {} : { workspace }),
    ...(state === undefined ? {} : { state }),
    ...(limit === undefined ? {} : { limit }),
  },
});
export const sessionOpen = ({ workspace, mode, session, ...options } = {}) => method("session.open", {
  ...options,
  params: {
    ...(options.params ?? {}),
    ...(workspace === undefined ? {} : { workspace }),
    ...(mode === undefined ? {} : { mode }),
    ...(session === undefined ? {} : { session }),
  },
});
export const sessionStatus = (session, { workspace, ...options } = {}) => method("session.status", { ...options, params: { ...(options.params ?? {}), session, ...(workspace === undefined ? {} : { workspace }) } });
export const sessionResult = (session, { workspace, ...options } = {}) => method("session.result", { ...options, params: { ...(options.params ?? {}), session, ...(workspace === undefined ? {} : { workspace }) } });
export const sessionWait = (session, { workspace, timeout, ...options } = {}) => method("session.wait", { ...options, params: { ...(options.params ?? {}), session, ...(workspace === undefined ? {} : { workspace }), ...(timeout === undefined ? {} : { timeout }) } });
export const sessionEvents = (session, { workspace, after, limit, ...options } = {}) => method("session.events", { ...options, params: { ...(options.params ?? {}), session, ...(workspace === undefined ? {} : { workspace }), ...(after === undefined ? {} : { after }), ...(limit === undefined ? {} : { limit }) } });
export const sessionRun = (session, prompt, { workspace, ...options } = {}) => method("session.run", { ...options, params: { ...(options.params ?? {}), session, prompt, ...(workspace === undefined ? {} : { workspace }) } });
export const sessionControl = (session, action, { workspace, ...options } = {}) => method(`session.${action}`, { ...options, params: { ...(options.params ?? {}), session, ...(workspace === undefined ? {} : { workspace }) } });
export const sessionClose = (session, options = {}) => sessionControl(session, "close", options);
export const sessionApproval = (session, approved, { workspace, ...options } = {}) => {
  validateSession(session);
  if (typeof approved !== "boolean") throw new TypeError("approved must be boolean");
  validateWorkspace(workspace);
  return method("session.approval", { ...options, params: { ...(options.params ?? {}), session, approved, ...(workspace === undefined ? {} : { workspace }) } });
};
export const configProfiles = (options = {}) => method("config.profiles", options);
export const configPolicy = ({ workspace, profile, mode, tools, excludeTools, noTools, ...options } = {}) => method("config.policy", {
  ...options,
  params: { ...(options.params ?? {}), ...(workspace === undefined ? {} : { workspace }), ...(profile === undefined ? {} : { profile }), ...(mode === undefined ? {} : { mode }), ...(tools === undefined ? {} : { tools }), ...(excludeTools === undefined ? {} : { exclude_tools: excludeTools }), ...(noTools === undefined ? {} : { no_tools: noTools }) },
});

export function interactive(workspace, { mode = "plan", executable = "forgecode", maxEvents = 2048 } = {}) {
  if (!Number.isInteger(maxEvents) || maxEvents < 1 || maxEvents > 100_000) throw new TypeError("maxEvents must be an integer between 1 and 100000");
  const child = spawn(executable, ["--workspace", workspace, "chat", "--mode", mode, "--jsonl"], { stdio: ["pipe", "pipe", "pipe"] });
  let buffer = ""; let stderr = ""; const events = []; const listeners = new Set();
  child.stdout.on("data", (chunk) => { buffer += chunk; const lines = buffer.split(/\r?\n/); buffer = lines.pop(); for (const line of lines) if (line.trim()) { try { const event = JSON.parse(line); events.push(event); if (events.length > maxEvents) events.splice(0, events.length - maxEvents); for (const listener of listeners) listener(event); } catch (error) { const event = { kind: "process_error", code: "invalid_json", message: error.message || "invalid JSON event" }; events.push(event); if (events.length > maxEvents) events.splice(0, events.length - maxEvents); for (const listener of listeners) listener(event); child.kill(); return; } } });
  child.stderr.on("data", (chunk) => { stderr += chunk.toString(); if (Buffer.byteLength(stderr, "utf8") > 256_000) stderr = stderr.slice(-128_000); });
  child.on("error", (error) => { const event = { kind: "process_error", code: "process_error", message: (error?.message || "interactive process failed").slice(0, 500) }; events.push(event); if (events.length > maxEvents) events.splice(0, events.length - maxEvents); for (const listener of listeners) listener(event); closed = true; });
  let closed = false;
  return {
    process: child, get stderr() { return stderr; },
    send(text) { if (closed) throw new ForgeCodeError("interactive process is closed", { code: "process_error" }); if (typeof text !== "string" || !text.trim() || text.length > 8000) throw new Error("message must be non-empty and bounded"); try { child.stdin.write(text.replace(/[\r\n]/g, " ") + "\n"); } catch (error) { throw new ForgeCodeError(error.message || "interactive process is unavailable", { code: "process_error" }); } },
    cancel() { this.send("/cancel"); }, pause() { this.send("/pause"); }, resume() { this.send("/resume"); }, close() { if (!closed) { try { child.stdin.write("/quit\n"); } catch {} closed = true; } },
    on(listener) { listeners.add(listener); return () => listeners.delete(listener); }, events,
    async closeAndWait(timeoutMs = 3000) {
      boundedNumber(timeoutMs, "timeoutMs");
      if (closed) return child.exitCode;
      closed = true;
      try { child.stdin.write("/quit\n"); } catch {}
      if (child.exitCode === null) {
        await new Promise((resolve) => {
          const timer = setTimeout(resolve, timeoutMs);
          child.once("close", () => { clearTimeout(timer); resolve(); });
        });
      }
      if (child.exitCode === null) child.kill();
      return child.exitCode;
    },
  };
}
