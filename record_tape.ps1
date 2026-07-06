# Record a Loom tape from the verified multi-feed source list.
# Usage: .\record_tape.ps1

$ErrorActionPreference = "Stop"

$python = "python"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$recorder = Join-Path $root "loom_tape_recorder_engine_ready.py"
$sourcesFile = Join-Path $root "loom_feed_sources.json"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$tapeFile = Join-Path $root "loom_stack_verified_$stamp.tape.ndjson"
$jsonFile = Join-Path $root "loom_stack_verified_$stamp.tape.json"

if (-not (Test-Path $recorder)) {
    throw "Recorder not found: $recorder"
}

if (-not (Test-Path $sourcesFile)) {
    throw "Source manifest not found: $sourcesFile"
}

Write-Host "=== Loom Tape Recorder ===`n" -ForegroundColor Cyan
Write-Host "Recorder: $recorder"
Write-Host "Sources:  $sourcesFile"
Write-Host "NDJSON:   $tapeFile"
Write-Host "JSON:     $jsonFile`n"

$env:LOOM_SOURCES_FILE = $sourcesFile
$env:LOOM_TAPE_OUT = $tapeFile
$env:LOOM_JSON_OUT = $jsonFile

$output = @'
import json
import os
from pathlib import Path

from loom_tape_recorder_engine_ready import MultiRecorderConfig, SourceSpec, record_multi

sources_path = Path(os.environ["LOOM_SOURCES_FILE"])
out_ndjson = Path(os.environ["LOOM_TAPE_OUT"])
out_json = Path(os.environ["LOOM_JSON_OUT"])

feeds = json.loads(sources_path.read_text(encoding="utf-8"))
specs = []
for feed in feeds:
    specs.append(
        SourceSpec(
            source_type=feed.get("kind", "rss"),
            target=feed["url"],
            label=f'{feed["category"]} | {feed["name"]}',
            enabled=True,
        )
    )

cfg = MultiRecorderConfig(
    sources=specs,
    tick_sec=1,
    duration_sec=0,
    back_sec=0,
    out_ndjson=out_ndjson,
    out_json=out_json,
    write_json=True,
    print_rows=False,
)

emitted, ticks = record_multi(cfg)
result = {
    "sources": len(specs),
    "emitted": emitted,
    "ticks": ticks,
    "ndjson": str(out_ndjson),
    "json": str(out_json),
}
print("RESULT_JSON=" + json.dumps(result, ensure_ascii=False))
'@ | & $python -

$outputLines = @($output)
$resultLine = $outputLines | Where-Object { $_ -like "RESULT_JSON=*" } | Select-Object -Last 1

if ($LASTEXITCODE -ne 0) {
    $outputLines | ForEach-Object { Write-Host $_ }
    throw "Recorder run failed with exit code $LASTEXITCODE"
}

$outputLines | Where-Object { $_ -notlike "RESULT_JSON=*" } | ForEach-Object { Write-Host $_ }

if (-not $resultLine) {
    throw "Recorder completed but did not return a summary line."
}

$result = $resultLine.Substring("RESULT_JSON=".Length) | ConvertFrom-Json
$ndjsonRows = if (Test-Path $tapeFile) { (Get-Content $tapeFile | Measure-Object).Count } else { 0 }
$jsonRows = if (Test-Path $jsonFile) { ((Get-Content $jsonFile -Raw | ConvertFrom-Json) | Measure-Object).Count } else { 0 }

Write-Host "`n=== Done ===" -ForegroundColor Cyan
Write-Host "Sources polled: $($result.sources)"
Write-Host "Rows emitted:   $($result.emitted)"
Write-Host "Ticks:          $($result.ticks)"
Write-Host "NDJSON rows:    $ndjsonRows"
Write-Host "JSON rows:      $jsonRows"
Write-Host "Tape saved to:  $tapeFile"
Write-Host "JSON saved to:  $jsonFile"
