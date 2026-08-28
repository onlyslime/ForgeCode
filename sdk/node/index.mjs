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

export class ForgeCodeError extends Error {
  constructor(message, { code = "sdk_error", envelope = null, exitCode = null } = {}) {
    super(message); this.name = "ForgeCodeError"; this.code = code; this.envelope = envelope; this.exitCode = exitCode;
  }
}

export function invoke(argv = [], { cwd, executable = "forgecode", method, params = {}, id, timeoutMs = 30000, maxOutputBytes = 2_000_000 } = {}) {
  validateArgv(argv);
  if (method !== undefined) validateParams(params);
  boundedNumber(timeoutMs, "timeoutMs"); boundedNumber(maxOutputBytes, "maxOutputBytes", { integer: true });
  return new Promise((resolve, reject) => {
    const rpc = method !== undefined;
    const child = spawn(executable, rpc ? ["rpc"] : [...argv, "--jsonl"], { cwd, stdio: ["pipe", "pipe", "pipe"] });
    let out = "";
    let err = "";
    let settled = false;
    const timer = setTimeout(() => { if (!settled) { child.kill(); settled = true; reject(new ForgeCodeError("request timed out", { code: "timeout" })); } }, Math.max(1, timeoutMs));
    if (rpc) child.stdin.write(JSON.stringify({ argv: [], method, params, ...(id === undefined ? {} : { id }) }) + "\n");
    child.stdin.end();
    child.stdout.on("data", (chunk) => { out += chunk; if (Buffer.byteLength(out) > maxOutputBytes && !settled) { child.kill(); settled = true; clearTimeout(timer); reject(new ForgeCodeError("response exceeds output limit", { code: "output_limit" })); } });
    child.stderr.on("data", (chunk) => { err += chunk; });
    child.on("error", (error) => { if (!settled) { settled = true; clearTimeout(timer); reject(new ForgeCodeError(error.message || "process failed", { code: "process_error" })); } });
    child.on("close", (code) => {
      if (settled) return;
      settled = true; clearTimeout(timer);
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
    const timer = setTimeout(() => { if (!settled) { child.kill(); settled = true; reject(new ForgeCodeError("request timed out", { code: "timeout" })); } }, Math.max(1, options.timeoutMs ?? 30000));
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
      if (settled) return; settled = true; clearTimeout(timer);
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

export const trust = (action = "status", options = {}) => invoke(["trust", action], options);
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
export const sessionInspect = (session, options = {}) => method("session.inspect", { ...options, params: { ...(options.params ?? {}), session } });
export const sessionTree = (options = {}) => method("session.tree", options);
export const sessionOpen = (options = {}) => method("session.open", options);
export const sessionStatus = (session, options = {}) => method("session.status", { ...options, params: { ...(options.params ?? {}), session } });
export const sessionEvents = (session, options = {}) => method("session.events", { ...options, params: { ...(options.params ?? {}), session, ...(options.after === undefined ? {} : { after: options.after }), ...(options.limit === undefined ? {} : { limit: options.limit }) } });
export const sessionRun = (session, prompt, options = {}) => method("session.run", { ...options, params: { ...(options.params ?? {}), session, prompt } });
export const sessionControl = (session, action, options = {}) => method(`session.${action}`, { ...options, params: { ...(options.params ?? {}), session } });
export const sessionClose = (session, options = {}) => sessionControl(session, "close", options);
export const sessionApproval = (session, approved, options = {}) => method("session.approval", { ...options, params: { ...(options.params ?? {}), session, approved } });

export function interactive(workspace, { mode = "plan", executable = "forgecode", maxEvents = 2048 } = {}) {
  if (!Number.isInteger(maxEvents) || maxEvents < 1 || maxEvents > 100_000) throw new TypeError("maxEvents must be an integer between 1 and 100000");
  const child = spawn(executable, ["--workspace", workspace, "chat", "--mode", mode, "--jsonl"], { stdio: ["pipe", "pipe", "pipe"] });
  let buffer = ""; const events = []; const listeners = new Set();
  child.stdout.on("data", (chunk) => { buffer += chunk; const lines = buffer.split(/\r?\n/); buffer = lines.pop(); for (const line of lines) if (line.trim()) { const event = JSON.parse(line); events.push(event); if (events.length > maxEvents) events.splice(0, events.length - maxEvents); for (const listener of listeners) listener(event); } });
  return {
    process: child,
    send(text) { if (typeof text !== "string" || !text.trim() || text.length > 8000) throw new Error("message must be non-empty and bounded"); child.stdin.write(text.replace(/[\r\n]/g, " ") + "\n"); },
    cancel() { this.send("/cancel"); }, pause() { this.send("/pause"); }, resume() { this.send("/resume"); }, close() { this.send("/quit"); },
    on(listener) { listeners.add(listener); return () => listeners.delete(listener); }, events,
  };
}
