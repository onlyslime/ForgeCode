#!/usr/bin/env node

const { existsSync } = require("node:fs")
const { join } = require("node:path")
const { spawn } = require("node:child_process")

const platform = process.platform
const arch = process.arch
const suffix = platform === "win32" ? ".exe" : ""
const target = join(__dirname, "..", "dist", `${platform}-${arch}`, `forgecode${suffix}`)

if (!existsSync(target)) {
  console.error(`ForgeCode binary is not included for ${platform}/${arch}.`)
  console.error(`Expected: ${target}`)
  console.error("Install a release containing this platform or run ForgeCode from source with uv.")
  process.exitCode = 1
} else {
  const child = spawn(target, process.argv.slice(2), { stdio: "inherit", windowsHide: false })
  child.on("error", (error) => { console.error(`Could not start ForgeCode: ${error.message}`); process.exitCode = 1 })
  child.on("exit", (code, signal) => { process.exitCode = code ?? (signal ? 1 : 0) })
}
