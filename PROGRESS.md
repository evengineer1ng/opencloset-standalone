# OpenCloset Development Progress Log

## Session: 2026-05-03

### Problem Statement
1. **Auto-scroll snap**: Chat snaps to bottom instead of letting user scroll at their own pace
2. **80k token tool calls**: One tool call brings in 80k tokens, breaking the chat and wasting tokens

### Investigation Status
- [ ] Find root cause of 80k token tool responses
- [ ] Add output truncation to tool executor
- [ ] Fix auto-scroll behavior in chat UI

### Findings So Far
- Project structure identified: provider.py, storage.py, api/, ui/
- Need to examine tool execution pipeline to find where outputs are unbounded
