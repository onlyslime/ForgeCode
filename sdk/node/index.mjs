/** Minimal Node SDK for ForgeCode's JSONL RPC/CLI envelope. */
import { spawn } from "node:child_process";

export class ForgeCodeError extends Error {
  constructor(message, { code = "sdk_error", envelope = null, exitCode = null } = {}) {
    super(message); this.name = "ForgeCodeError"; this.code = code; this.envelope = envelope; this.exitCode = exitCode;
  }
}

export function invoke(argv = [], { cwd, executable = "forgecode", method, params = {}, id, timeoutMs = 30000, maxOutputBytes = 2_000_000 } = {}) {
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
    child.on("error", (error) => { if (!settled) { settled = true; clearTimeout(timer); reject(error); } });
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
  return new Promise((resolve, reject) => {
    const rpc = options.method !== undefined;
    const child = spawn(options.executable ?? "forgecode", rpc ? ["rpc"] : [...argv, "--jsonl"], { cwd: options.cwd, stdio: ["pipe", "pipe", "pipe"] });
    if (rpc) { child.stdin.write(JSON.stringify({ argv: [], method: options.method, params: options.params ?? {}, ...(options.id === undefined ? {} : { id: options.id }) }) + "\n"); child.stdin.end(); }
    let buffer = ""; const events = []; let err = "";
    child.stdout.on("data", (chunk) => { buffer += chunk; const lines = buffer.split(/\r?\n/); buffer = lines.pop(); for (const line of lines) if (line.trim()) events.push(JSON.parse(line)); });
    child.stderr.on("data", (chunk) => { err += chunk; });
    child.on("error", reject);
    child.on("close", (code) => { if (buffer.trim()) events.push(JSON.parse(buffer)); if (!events.length) return reject(new Error(err.trim() || `forgecode exited ${code}`)); resolve({ events, process_exit_code: code }); });
  });
}

export const trust = (action = "status", options = {}) => invoke(["trust", action], options);
export const login = (options = {}) => invoke(["login"], options);
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

export function interactive(workspace, { mode = "plan", executable = "forgecode" } = {}) {
  const child = spawn(executable, ["--workspace", workspace, "chat", "--mode", mode, "--jsonl"], { stdio: ["pipe", "pipe", "pipe"] });
  let buffer = ""; const events = []; const listeners = new Set();
  child.stdout.on("data", (chunk) => { buffer += chunk; const lines = buffer.split(/\r?\n/); buffer = lines.pop(); for (const line of lines) if (line.trim()) { const event = JSON.parse(line); events.push(event); for (const listener of listeners) listener(event); } });
  return {
    process: child,
    send(text) { if (typeof text !== "string" || !text.trim() || text.length > 8000) throw new Error("message must be non-empty and bounded"); child.stdin.write(text.replace(/[\r\n]/g, " ") + "\n"); },
    cancel() { this.send("/cancel"); }, pause() { this.send("/pause"); }, resume() { this.send("/resume"); }, close() { this.send("/quit"); },
    on(listener) { listeners.add(listener); return () => listeners.delete(listener); }, events,
  };
}
