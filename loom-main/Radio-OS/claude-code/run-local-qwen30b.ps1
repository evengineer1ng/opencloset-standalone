$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$bunBin = Join-Path $env:USERPROFILE '.bun\bin'
$env:PATH = "$bunBin;$env:PATH"

$proxyLog = Join-Path $repoRoot 'proxy.log'
$proxyErr = Join-Path $repoRoot 'proxy.err.log'

$listener = Get-NetTCPConnection -LocalPort 4000 -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
  if (Test-Path $proxyLog) { Remove-Item $proxyLog -Force }
  if (Test-Path $proxyErr) { Remove-Item $proxyErr -Force }

  Start-Process `
    -FilePath 'node' `
    -ArgumentList 'scripts/ollama-anthropic-proxy.mjs' `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $proxyLog `
    -RedirectStandardError $proxyErr | Out-Null

  Start-Sleep -Seconds 2
}

$env:DISABLE_AUTOUPDATER = '1'
$env:ANTHROPIC_API_KEY = 'local-dev-key'
$env:ANTHROPIC_BASE_URL = 'http://127.0.0.1:4000'
$env:ANTHROPIC_DEFAULT_SONNET_MODEL = 'qwen3-coder:30b'
$env:CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS = '1'
$env:DISABLE_PROMPT_CACHING = '1'
$env:CI = '1'

Push-Location $repoRoot
try {
  if ($args.Count -gt 0) {
    & bun --preload src/shims/macro.ts src/entrypoints/cli.tsx @args
  } else {
    & bun --preload src/shims/macro.ts src/entrypoints/cli.tsx
  }
} finally {
  Pop-Location
}
