import http from 'node:http'
import { randomUUID } from 'node:crypto'

const PORT = Number(process.env.OLLAMA_ANTHROPIC_PROXY_PORT || 4000)
const OLLAMA_BASE_URL =
  process.env.OLLAMA_BASE_URL || 'http://127.0.0.1:11434'

function sendJson(res, status, body) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' })
  res.end(JSON.stringify(body))
}

function sse(res, event, data) {
  res.write(`event: ${event}\n`)
  res.write(`data: ${JSON.stringify(data)}\n\n`)
}

function anthropicError(message, type = 'invalid_request_error') {
  return { type: 'error', error: { type, message } }
}

function blockText(block) {
  if (!block) return ''
  if (typeof block === 'string') return block
  if (block.type === 'text') return block.text || ''
  if (block.type === 'tool_result') {
    if (typeof block.content === 'string') return block.content
    if (Array.isArray(block.content)) {
      return block.content
        .map(item => (item?.type === 'text' ? item.text || '' : JSON.stringify(item)))
        .join('\n')
    }
    return JSON.stringify(block.content ?? '')
  }
  if (block.type === 'tool_use') {
    return `[tool_use ${block.name}] ${JSON.stringify(block.input ?? {})}`
  }
  return JSON.stringify(block)
}

function flushUserBuffer(messages, state) {
  if (!state.userParts.length) return
  messages.push({
    role: 'user',
    content: state.userParts.join('\n').trim() || ' ',
  })
  state.userParts = []
}

function flushAssistantBuffer(messages, state) {
  if (!state.assistantText && !state.toolCalls.length) return
  messages.push({
    role: 'assistant',
    content: state.assistantText || '',
    ...(state.toolCalls.length ? { tool_calls: state.toolCalls } : {}),
  })
  state.assistantText = ''
  state.toolCalls = []
}

function toOpenAiMessages(anthropicMessages) {
  const messages = []
  const state = {
    userParts: [],
    assistantText: '',
    toolCalls: [],
  }

  for (const message of anthropicMessages || []) {
    const content = Array.isArray(message.content)
      ? message.content
      : [{ type: 'text', text: String(message.content ?? '') }]

    if (message.role === 'user') {
      for (const block of content) {
        if (block.type === 'tool_result') {
          flushUserBuffer(messages, state)
          messages.push({
            role: 'tool',
            tool_call_id: block.tool_use_id,
            content: blockText(block),
          })
        } else {
          state.userParts.push(blockText(block))
        }
      }
      flushUserBuffer(messages, state)
      continue
    }

    if (message.role === 'assistant') {
      flushUserBuffer(messages, state)
      for (const block of content) {
        if (block.type === 'tool_use') {
          state.toolCalls.push({
            id: block.id,
            type: 'function',
            function: {
              name: block.name,
              arguments: JSON.stringify(block.input ?? {}),
            },
          })
        } else {
          const text = blockText(block)
          if (text) {
            state.assistantText += (state.assistantText ? '\n' : '') + text
          }
        }
      }
      flushAssistantBuffer(messages, state)
      continue
    }
  }

  flushUserBuffer(messages, state)
  flushAssistantBuffer(messages, state)
  return messages
}

function toSystemString(system) {
  if (!system) return undefined
  if (typeof system === 'string') return system
  if (Array.isArray(system)) {
    return system
      .map(item => (typeof item === 'string' ? item : item?.text || ''))
      .filter(Boolean)
      .join('\n\n')
  }
  return undefined
}

function toOpenAiTools(tools) {
  if (!Array.isArray(tools) || !tools.length) return undefined
  return tools.map(tool => ({
    type: 'function',
    function: {
      name: tool.name,
      description: tool.description || '',
      parameters: tool.input_schema || { type: 'object', properties: {} },
    },
  }))
}

function toOpenAiToolChoice(toolChoice) {
  if (!toolChoice || toolChoice === 'auto') return 'auto'
  if (toolChoice.type === 'auto') return 'auto'
  if (toolChoice.type === 'tool' && toolChoice.name) {
    return { type: 'function', function: { name: toolChoice.name } }
  }
  return 'auto'
}

function textResponseBody(model, text, usage = {}) {
  return {
    id: `msg_${randomUUID()}`,
    type: 'message',
    role: 'assistant',
    model,
    content: [{ type: 'text', text }],
    stop_reason: 'end_turn',
    stop_sequence: null,
    usage: {
      input_tokens: usage.prompt_tokens || 0,
      output_tokens: usage.completion_tokens || 0,
    },
  }
}

function normalizeToolCallObject(parsed) {
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  const name =
    parsed.name ||
    parsed.tool ||
    parsed.tool_name ||
    parsed.function?.name
  const rawArgs =
    parsed.arguments ??
    parsed.args ??
    parsed.input ??
    parsed.parameters ??
    parsed.function?.arguments ??
    {}
  if (!name || typeof name !== 'string') return null
  return {
    id: parsed.id || `call_${randomUUID().slice(0, 8)}`,
    type: 'function',
    function: {
      name,
      arguments:
        typeof rawArgs === 'string' ? rawArgs : JSON.stringify(rawArgs ?? {}),
    },
  }
}

function extractToolCallsFromText(text) {
  if (!text || typeof text !== 'string') return null
  const candidates = [text.trim()]
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)\s*```/i)
  if (fenced?.[1]) candidates.push(fenced[1].trim())

  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate)
      if (Array.isArray(parsed)) {
        const calls = parsed.map(normalizeToolCallObject).filter(Boolean)
        if (calls.length) return calls
      } else {
        const call = normalizeToolCallObject(parsed)
        if (call) return [call]
      }
    } catch {}
  }
  return null
}

function toolResponseBody(model, toolCalls, usage = {}) {
  return {
    id: `msg_${randomUUID()}`,
    type: 'message',
    role: 'assistant',
    model,
    content: toolCalls.map(call => ({
      type: 'tool_use',
      id: call.id || `toolu_${randomUUID()}`,
      name: call.function?.name || 'unknown_tool',
      input: safeJson(call.function?.arguments),
    })),
    stop_reason: 'tool_use',
    stop_sequence: null,
    usage: {
      input_tokens: usage.prompt_tokens || 0,
      output_tokens: usage.completion_tokens || 0,
    },
  }
}

function safeJson(text) {
  try {
    return text ? JSON.parse(text) : {}
  } catch {
    return { _raw: text || '' }
  }
}

async function handleModels(_req, res) {
  const upstream = await fetch(`${OLLAMA_BASE_URL}/api/tags`)
  if (!upstream.ok) {
    const text = await upstream.text()
    sendJson(res, upstream.status, anthropicError(text, 'api_error'))
    return
  }
  const data = await upstream.json()
  sendJson(res, 200, {
    data: (data.models || []).map(model => ({
      type: 'model',
      id: model.name,
      display_name: model.name,
      created_at: model.modified_at,
    })),
    has_more: false,
    first_id: data.models?.[0]?.name || null,
    last_id: data.models?.at?.(-1)?.name || null,
  })
}

async function handleMessages(req, res, body) {
  const model = body.model
  if (!model) {
    sendJson(res, 400, anthropicError('Missing required field: model'))
    return
  }

  const system = toSystemString(body.system)
  const messages = toOpenAiMessages(body.messages)
  const tools = toOpenAiTools(body.tools)
  const payload = {
    model,
    messages: system ? [{ role: 'system', content: system }, ...messages] : messages,
    stream: Boolean(body.stream),
    ...(typeof body.temperature === 'number' ? { temperature: body.temperature } : {}),
    ...(typeof body.max_tokens === 'number'
      ? { max_completion_tokens: body.max_tokens }
      : {}),
    ...(tools ? { tools } : {}),
    ...(tools ? { tool_choice: toOpenAiToolChoice(body.tool_choice) } : {}),
  }
  const forceBufferedStream =
    body.stream && process.env.OLLAMA_PROXY_FORCE_BUFFERED_STREAM === '1'

  if (!body.stream || forceBufferedStream) {
    const upstream = await fetch(`${OLLAMA_BASE_URL}/v1/chat/completions`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ...payload, stream: false }),
    })
    if (!upstream.ok) {
      const text = await upstream.text()
      sendJson(res, upstream.status, anthropicError(text, 'api_error'))
      return
    }
    const data = await upstream.json()
    const choice = data.choices?.[0]?.message || {}
    const inferredToolCalls =
      choice.tool_calls?.length
        ? choice.tool_calls
        : extractToolCallsFromText(choice.content || '')
    const response = inferredToolCalls?.length
      ? toolResponseBody(model, inferredToolCalls, data.usage)
      : textResponseBody(model, choice.content || '', data.usage)
    if (!body.stream) {
      sendJson(res, 200, response)
      return
    }

    res.writeHead(200, {
      'content-type': 'text/event-stream; charset=utf-8',
      'cache-control': 'no-cache',
      connection: 'keep-alive',
    })
    sse(res, 'message_start', {
      type: 'message_start',
      message: {
        id: response.id,
        type: 'message',
        role: 'assistant',
        model,
        content: [],
        stop_reason: null,
        stop_sequence: null,
        usage: {
          input_tokens: response.usage?.input_tokens || 0,
          output_tokens: 0,
        },
      },
    })

    response.content.forEach((block, index) => {
      if (block.type === 'text') {
        sse(res, 'content_block_start', {
          type: 'content_block_start',
          index,
          content_block: { type: 'text', text: '' },
        })
        sse(res, 'content_block_delta', {
          type: 'content_block_delta',
          index,
          delta: { type: 'text_delta', text: block.text || '' },
        })
        sse(res, 'content_block_stop', {
          type: 'content_block_stop',
          index,
        })
      } else if (block.type === 'tool_use') {
        sse(res, 'content_block_start', {
          type: 'content_block_start',
          index,
          content_block: {
            type: 'tool_use',
            id: block.id,
            name: block.name,
            input: {},
          },
        })
        sse(res, 'content_block_delta', {
          type: 'content_block_delta',
          index,
          delta: {
            type: 'input_json_delta',
            partial_json: JSON.stringify(block.input ?? {}),
          },
        })
        sse(res, 'content_block_stop', {
          type: 'content_block_stop',
          index,
        })
      }
    })
    sse(res, 'message_delta', {
      type: 'message_delta',
      delta: {
        stop_reason: response.stop_reason,
        stop_sequence: response.stop_sequence,
      },
      usage: { output_tokens: response.usage?.output_tokens || 0 },
    })
    sse(res, 'message_stop', { type: 'message_stop' })
    res.end()
    return
  }

  const upstream = await fetch(`${OLLAMA_BASE_URL}/v1/chat/completions`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!upstream.ok || !upstream.body) {
    const text = await upstream.text()
    sendJson(res, upstream.status || 500, anthropicError(text, 'api_error'))
    return
  }

  const messageId = `msg_${randomUUID()}`
  res.writeHead(200, {
    'content-type': 'text/event-stream; charset=utf-8',
    'cache-control': 'no-cache',
    connection: 'keep-alive',
  })

  sse(res, 'message_start', {
    type: 'message_start',
    message: {
      id: messageId,
      type: 'message',
      role: 'assistant',
      model,
      content: [],
      stop_reason: null,
      stop_sequence: null,
      usage: { input_tokens: 0, output_tokens: 0 },
    },
  })

  const reader = upstream.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let textIndex = null
  let nextIndex = 0
  const toolIndexMap = new Map()
  const toolState = new Map()
  let completionTokens = 0

  const ensureTextBlock = () => {
    if (textIndex !== null) return textIndex
    textIndex = nextIndex++
    sse(res, 'content_block_start', {
      type: 'content_block_start',
      index: textIndex,
      content_block: { type: 'text', text: '' },
    })
    return textIndex
  }

  const ensureToolBlock = toolCallIndex => {
    if (toolIndexMap.has(toolCallIndex)) return toolIndexMap.get(toolCallIndex)
    const anthropicIndex = nextIndex++
    toolIndexMap.set(toolCallIndex, anthropicIndex)
    toolState.set(toolCallIndex, { id: `toolu_${randomUUID()}`, name: '' })
    return anthropicIndex
  }

  const finalizeOpenBlocks = stopReason => {
    if (textIndex !== null) {
      sse(res, 'content_block_stop', {
        type: 'content_block_stop',
        index: textIndex,
      })
    }
    for (const anthropicIndex of toolIndexMap.values()) {
      sse(res, 'content_block_stop', {
        type: 'content_block_stop',
        index: anthropicIndex,
      })
    }
    sse(res, 'message_delta', {
      type: 'message_delta',
      delta: { stop_reason: stopReason, stop_sequence: null },
      usage: { output_tokens: completionTokens },
    })
    sse(res, 'message_stop', { type: 'message_stop' })
    res.end()
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n')
    buffer = parts.pop() || ''

    for (const line of parts) {
      const trimmed = line.trim()
      if (!trimmed.startsWith('data:')) continue
      const dataText = trimmed.slice(5).trim()
      if (dataText === '[DONE]') {
        finalizeOpenBlocks(toolIndexMap.size ? 'tool_use' : 'end_turn')
        return
      }
      let parsed
      try {
        parsed = JSON.parse(dataText)
      } catch {
        continue
      }

      const choice = parsed.choices?.[0]
      const delta = choice?.delta || {}
      if (parsed.usage?.completion_tokens) {
        completionTokens = parsed.usage.completion_tokens
      }

      if (delta.content) {
        const index = ensureTextBlock()
        sse(res, 'content_block_delta', {
          type: 'content_block_delta',
          index,
          delta: { type: 'text_delta', text: delta.content },
        })
      }

      if (Array.isArray(delta.tool_calls)) {
        for (const toolCall of delta.tool_calls) {
          const toolCallIndex = toolCall.index ?? 0
          const anthropicIndex = ensureToolBlock(toolCallIndex)
          const state = toolState.get(toolCallIndex)
          const name = toolCall.function?.name || state.name || 'unknown_tool'
          const id = toolCall.id || state.id
          if (!state.started) {
            state.started = true
            state.id = id
            state.name = name
            sse(res, 'content_block_start', {
              type: 'content_block_start',
              index: anthropicIndex,
              content_block: {
                type: 'tool_use',
                id,
                name,
                input: {},
              },
            })
          }
          if (toolCall.function?.name) {
            state.name = toolCall.function.name
          }
          if (toolCall.function?.arguments) {
            sse(res, 'content_block_delta', {
              type: 'content_block_delta',
              index: anthropicIndex,
              delta: {
                type: 'input_json_delta',
                partial_json: toolCall.function.arguments,
              },
            })
          }
        }
      }
    }
  }

  finalizeOpenBlocks(toolIndexMap.size ? 'tool_use' : 'end_turn')
}

function parseBody(req) {
  return new Promise((resolve, reject) => {
    let raw = ''
    req.on('data', chunk => {
      raw += chunk
      if (raw.length > 25 * 1024 * 1024) {
        reject(new Error('Request body too large'))
      }
    })
    req.on('end', () => {
      try {
        resolve(raw ? JSON.parse(raw) : {})
      } catch (error) {
        reject(error)
      }
    })
    req.on('error', reject)
  })
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url || '/', `http://${req.headers.host || '127.0.0.1'}`)
    console.log(`${req.method} ${url.pathname}${url.search}`)
    if (req.method === 'GET' && url.pathname === '/v1/models') {
      await handleModels(req, res)
      return
    }
    if (req.method === 'POST' && url.pathname === '/v1/messages') {
      const body = await parseBody(req)
      await handleMessages(req, res, body)
      return
    }
    if (req.method === 'GET' && url.pathname === '/health') {
      sendJson(res, 200, { ok: true })
      return
    }
    sendJson(res, 404, anthropicError('Not found', 'not_found_error'))
  } catch (error) {
    sendJson(
      res,
      500,
      anthropicError(error instanceof Error ? error.message : String(error), 'api_error'),
    )
  }
})

server.listen(PORT, '127.0.0.1', () => {
  console.log(
    `Ollama Anthropic proxy listening on http://127.0.0.1:${PORT} -> ${OLLAMA_BASE_URL}`,
  )
})
