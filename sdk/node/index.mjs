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
