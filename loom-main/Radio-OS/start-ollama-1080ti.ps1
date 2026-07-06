param(
  [switch]$ForceRestart
)

$ErrorActionPreference = 'Stop'

function Get-GpuInventory {
  $lines = & nvidia-smi --query-gpu=index,name,uuid,memory.total --format=csv,noheader 2>$null
  if (-not $lines) {
    throw 'nvidia-smi did not return any GPUs.'
  }

  $gpus = @()
  foreach ($line in $lines) {
    $parts = $line -split ',' | ForEach-Object { $_.Trim() }
    if ($parts.Count -lt 4) {
      continue
    }
    $gpus += [pscustomobject]@{
      Index = [int]$parts[0]
      Name = $parts[1]
      Uuid = $parts[2]
      Memory = $parts[3]
    }
  }

  if (-not $gpus) {
    throw 'Unable to parse GPU inventory from nvidia-smi.'
  }

  return $gpus
}

function Get-PreferredGpu($gpus) {
  $preferred = $gpus | Where-Object { $_.Name -match '1080\s*Ti' } | Select-Object -First 1
  if ($preferred) {
    return $preferred
  }

  if ($gpus.Count -gt 1) {
    return $gpus[1]
  }

  return $gpus[0]
}

$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
  throw 'Ollama is not installed or not on PATH.'
}

$gpus = Get-GpuInventory
$targetGpu = Get-PreferredGpu $gpus

Write-Host "Target GPU: index=$($targetGpu.Index) name=$($targetGpu.Name) uuid=$($targetGpu.Uuid)"

if ($ForceRestart) {
  $procs = Get-Process ollama -ErrorAction SilentlyContinue
  if ($procs) {
    $procs | Stop-Process -Force
    Start-Sleep -Seconds 2
  }
}

$listener = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue
if ($listener -and -not $ForceRestart) {
  Write-Host 'Ollama is already listening on 127.0.0.1:11434. Use -ForceRestart to re-pin it.'
  exit 0
}

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $ollama.Source
$psi.Arguments = 'serve'
$psi.WorkingDirectory = Split-Path -Parent $ollama.Source
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.EnvironmentVariables['CUDA_VISIBLE_DEVICES'] = $targetGpu.Uuid
$psi.EnvironmentVariables['OLLAMA_HOST'] = '127.0.0.1:11434'

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
$null = $proc.Start()

$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Milliseconds 500
  try {
    $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -Method Get -TimeoutSec 2
    $healthy = $true
    break
  } catch {}
}

if (-not $healthy) {
  throw 'Ollama did not become ready on 127.0.0.1:11434.'
}

Write-Host "Ollama is running on $($targetGpu.Name) via CUDA_VISIBLE_DEVICES=$($targetGpu.Uuid)"