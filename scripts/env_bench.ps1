# Load API key + bench paths for SodaMem frozen runs (PowerShell).
# Usage:
#   cd ...\project\SodaMem-dev-main
#   . .\scripts\env_bench.ps1

$ErrorActionPreference = "Stop"

# scripts/ → SodaMem-dev-main
$RepoRoot = Split-Path $PSScriptRoot -Parent
# Layout A: <Workspace>/SodaMem-dev-main
# Layout B: <Workspace>/project/SodaMem-dev-main  (current)
$Parent = Split-Path $RepoRoot -Parent
$Workspace = $Parent
if ((Split-Path $RepoRoot -Leaf) -eq "SodaMem-dev-main" -and (Split-Path $Parent -Leaf) -eq "project") {
    $Workspace = Split-Path $Parent -Parent
}

$ApiEnv = Join-Path $Workspace "api\.env"
if (-not (Test-Path $ApiEnv)) {
    $ApiEnv = Join-Path $Parent "api\.env"
}
if (-not (Test-Path $ApiEnv)) {
    throw "Missing api\.env under workspace — need DEEPSEEK_API_KEY (looked in $Workspace)"
}

Get-Content $ApiEnv | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $i = $line.IndexOf("=")
    if ($i -lt 1) { return }
    $k = $line.Substring(0, $i).Trim()
    $v = $line.Substring($i + 1).Trim()
    Set-Item -Path "Env:$k" -Value $v
}

if (-not $env:DEEPSEEK_API_KEY) {
    throw "DEEPSEEK_API_KEY not set after reading api\.env"
}

$env:SODAMEM_REPO = $RepoRoot
$env:PYTHONPATH = $RepoRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$BenchData = Join-Path $Parent "sodamem_databack\bench-data"
if (-not (Test-Path $BenchData)) {
    $BenchData = Join-Path $Workspace "project\sodamem_databack\bench-data"
}
if (-not (Test-Path $BenchData)) {
    $BenchData = Join-Path $Workspace "sodamem_databack\bench-data"
}
$env:SODAMEM_BENCH_DATA = $BenchData

$Stores = Join-Path $Workspace "data\longmemeval_s_500_Hobs"
if (-not (Test-Path $Stores)) {
    $Stores = Join-Path $Parent "longmemeval_s_500_Hobs"
}
$env:SODAMEM_BENCH_STORES = $Stores
$env:SODAMEM_BENCH_RESULTS = Join-Path $RepoRoot "results"

if (-not $env:SODAMEM_BENCH_MODEL) {
    $env:SODAMEM_BENCH_MODEL = "deepseek-v4-flash"
}
if (-not $env:SODAMEM_BENCH_BASE_URL) {
    $env:SODAMEM_BENCH_BASE_URL = "https://api.deepseek.com"
}

New-Item -ItemType Directory -Force -Path $env:SODAMEM_BENCH_RESULTS | Out-Null

Write-Host "env ready:"
Write-Host "  SODAMEM_REPO=$env:SODAMEM_REPO"
Write-Host "  SODAMEM_BENCH_DATA=$env:SODAMEM_BENCH_DATA"
Write-Host "  SODAMEM_BENCH_STORES=$env:SODAMEM_BENCH_STORES"
Write-Host "  SODAMEM_BENCH_MODEL=$env:SODAMEM_BENCH_MODEL"
Write-Host "  DEEPSEEK_API_KEY=***set***"
Write-Host ""
Write-Host "Opt arm (Plan B):"
Write-Host "  python -m sodamem_opt.unit_smoke"
Write-Host "  python -m sodamem_opt.run_frozen --only q007,q028,q034,q035,q039,q055,q116 --out results\opt_miss7 --concurrency 2"
Write-Host "  python -m sodamem_opt.run_frozen --out results\opt_s500 --concurrency 4"
