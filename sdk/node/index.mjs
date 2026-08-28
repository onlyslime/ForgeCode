/** Minimal Node SDK for ForgeCode's JSONL RPC/CLI envelope. */
import { spawn } from "node:child_process";

export function invoke(argv = [], { cwd, executable = "forgecode" } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, [...argv, "--jsonl"], { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    let err = "";
    child.stdout.on("data", (chunk) => { out += chunk; });
    child.stderr.on("data", (chunk) => { err += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      const line = out.trim().split(/\r?\n/).filter(Boolean).pop();
      if (!line) return reject(new Error(err.trim() || `forgecode exited ${code}`));
      try {
        const envelope = JSON.parse(line);
        resolve({ ...envelope, process_exit_code: code });
      } catch (error) { reject(error); }
    });
  });
}

export function invokeStream(argv = [], options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(options.executable ?? "forgecode", [...argv, "--jsonl"], { cwd: options.cwd, stdio: ["ignore", "pipe", "pipe"] });
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
