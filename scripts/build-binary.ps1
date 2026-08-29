param([string]$Output = "packaging/npm/dist")
$ErrorActionPreference = "Stop"
uv run --with pyinstaller pyinstaller --noconfirm --clean --onefile --name forgecode --paths src packaging/entrypoint.py
$target = Join-Path $Output "$(node -p 'process.platform')-$(node -p 'process.arch')"
New-Item -ItemType Directory -Force $target | Out-Null
Copy-Item dist/forgecode.exe (Join-Path $target "forgecode.exe") -Force
Write-Output "Built $target/forgecode.exe"
