$env:LOOM_LLM = "openai"
$env:LOOM_LLM_BASE = "http://127.0.0.1:9080/v1"
$env:LOOM_LLM_KEY = "dummy"
$env:LOOM_LLM_OPENAI_MODE = "completion"

python "$PSScriptRoot\atlas_courtroom.py" `
  --atlas-dir "$PSScriptRoot\..\bench\atlas_recall\emb_bank" `
  --raw-model "qwen3.6-27b" `
  --outgress-model "qwen3.6-27b" `
  $args
