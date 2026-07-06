$ErrorActionPreference = 'Stop'

$workspaceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$claudeRepo = Join-Path $workspaceRoot 'claude-code'
$bunBin = Join-Path $env:USERPROFILE '.bun\bin'
$env:PATH = "$bunBin;$env:PATH"
$env:OLLAMA_PROXY_FORCE_BUFFERED_STREAM = '1'

if (-not (Test-Path $claudeRepo)) {
  throw "Expected cloned repo at $claudeRepo"
}

$proxyLog = Join-Path $claudeRepo 'proxy.log'
$proxyErr = Join-Path $claudeRepo 'proxy.err.log'

$listener = Get-NetTCPConnection -LocalPort 4000 -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
  if (Test-Path $proxyLog) { Remove-Item $proxyLog -Force }
  if (Test-Path $proxyErr) { Remove-Item $proxyErr -Force }

  Start-Process `
    -FilePath 'node' `
    -ArgumentList (Join-Path $claudeRepo 'scripts/ollama-anthropic-proxy.mjs') `
    -WorkingDirectory $claudeRepo `
    -RedirectStandardOutput $proxyLog `
    -RedirectStandardError $proxyErr | Out-Null

  $healthy = $false
  for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    try {
      $resp = Invoke-RestMethod -Uri 'http://127.0.0.1:4000/health' -Method Get -TimeoutSec 2
      if ($resp.ok -eq $true) {
        $healthy = $true
        break
      }
    } catch {}
  }

  if (-not $healthy) {
    throw "Local Ollama proxy failed to start. Check $proxyErr"
  }
}

$env:DISABLE_AUTOUPDATER = '1'
$env:ANTHROPIC_API_KEY = 'local-dev-key'
$env:ANTHROPIC_BASE_URL = 'http://127.0.0.1:4000'
$env:ANTHROPIC_DEFAULT_SONNET_MODEL = 'qwen2.5-coder:14b'
$env:CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS = '1'
$env:DISABLE_PROMPT_CACHING = '1'
$env:CI = '1'

Push-Location $workspaceRoot
try {
  if ($args.Count -gt 0) {
    & bun --preload (Join-Path $claudeRepo 'src/shims/macro.ts') (Join-Path $claudeRepo 'src/entrypoints/cli.tsx') @args
  } else {
    & bun --preload (Join-Path $claudeRepo 'src/shims/macro.ts') (Join-Path $claudeRepo 'src/entrypoints/cli.tsx')
  }
} finally {
  Pop-Location
}
