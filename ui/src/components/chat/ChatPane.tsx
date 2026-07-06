import { useEffect, useRef, useState } from "react";
import "./ChatPane.css";

export interface ToolStepData {
  toolName: string;
  summary: string;
  status: "running" | "success" | "error" | "interrupted";
  detail?: string;
}

export interface ChatMessageView {
  id: string;
  role: "user" | "assistant" | "system" | "tool_step";
  content: string;
  timestamp: string;
  toolUse?: { name: string; result: string };
  planRef?: string;
  isStreaming?: boolean;
  toolStep?: ToolStepData;
}

interface ChatPaneProps {
  sessionTitle: string;
  messages: ChatMessageView[];
  isSending: boolean;
  errorMessage: string | null;
  onSend: (content: string) => void;
}

type RenderItem =
  | { kind: "message"; msg: ChatMessageView }
  | { kind: "tool_group"; steps: ChatMessageView[]; key: string };

function buildRenderItems(messages: ChatMessageView[]): RenderItem[] {
  const items: RenderItem[] = [];
  let i = 0;
  while (i < messages.length) {
    if (messages[i].role === "tool_step") {
      const steps: ChatMessageView[] = [];
      const key = messages[i].id;
      while (i < messages.length && messages[i].role === "tool_step") {
        steps.push(messages[i]);
        i++;
      }
      items.push({ kind: "tool_group", steps, key });
    } else {
      items.push({ kind: "message", msg: messages[i] });
      i++;
    }
  }
  return items;
}

export default function ChatPane({
  sessionTitle,
  messages,
  isSending,
  errorMessage,
  onSend,
}: ChatPaneProps) {
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    // Only auto-scroll if the user is near the bottom (within 150px)
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 150;
    if (nearBottom) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages]);

  const handleSend = () => {
    const nextInput = input.trim();
    if (!nextInput || isSending) {
      return;
    }
    onSend(nextInput);
    setInput("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const renderItems = buildRenderItems(messages);

  return (
    <div className="chat-pane">
      <div ref={messagesContainerRef} className="chat-messages">
        {renderItems.map((item) => {
          if (item.kind === "tool_group") {
            return (
              <div key={item.key} className="tool-step-group">
                {item.steps.map((step) => (
                  <ToolStepRow key={step.id} step={step.toolStep!} />
                ))}
              </div>
            );
          }
          return <ChatMessageBubble key={item.msg.id} msg={item.msg} />;
        })}
        <div ref={messagesEndRef} />
      </div>
      <div className="chat-composer">
        <div className="chat-composer-inner">
          <textarea
            className="chat-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={`Message in "${sessionTitle}"...`}
            rows={1}
            disabled={isSending}
          />
          <button className="chat-send-btn" onClick={handleSend} disabled={isSending}>
            {isSending ? "..." : "↑"}
          </button>
        </div>
        {errorMessage && <div className="chat-error-banner">{errorMessage}</div>}
      </div>
    </div>
  );
}

function ToolStepRow({ step }: { step: ToolStepData }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = Boolean(step.detail);

  const statusIcon =
    step.status === "running"
      ? "○"
      : step.status === "success"
        ? "✓"
        : step.status === "interrupted"
          ? "⊘"
          : "✗";

  return (
    <div className={`tool-step-row ${step.status}${hasDetail ? " has-detail" : ""}`}>
      <div
        className="tool-step-main"
        onClick={() => hasDetail && setExpanded((e) => !e)}
        title={hasDetail ? (expanded ? "Collapse" : "Expand") : undefined}
      >
        <span className={`tool-step-icon ${step.status}`}>{statusIcon}</span>
        <span className="tool-step-name">{step.toolName}</span>
        <span className="tool-step-summary">{step.summary}</span>
        <span className={`tool-step-badge ${step.status}`}>{step.status}</span>
        {hasDetail && <span className="tool-step-expand">{expanded ? "▲" : "▼"}</span>}
      </div>
      {expanded && step.detail && <pre className="tool-step-detail">{step.detail}</pre>}
    </div>
  );
}

function ChatMessageBubble({ msg }: { msg: ChatMessageView }) {
  const roleClass = msg.role === "user" ? "user" : msg.role === "assistant" ? "assistant" : "system";
  const avatarContent = msg.role === "user" ? "👤" : msg.role === "assistant" ? "🤖" : "·";
  const avatarClass =
    msg.role === "user" ? "user-avatar" : msg.role === "assistant" ? "assistant-avatar" : "system-avatar";

  return (
    <div className={`chat-message ${roleClass}`}>
      <div className={`chat-msg-avatar ${avatarClass}`}>{avatarContent}</div>
      <div className="chat-msg-content">
        {msg.isStreaming && (
          <div className="run-state-badge">
            <span className="run-state-dot" />
            running
          </div>
        )}
        <div className="chat-msg-bubble">
          {msg.content}
          {msg.isStreaming && <span className="chat-stream-cursor">▌</span>}
        </div>
        {msg.toolUse && (
          <div className="tool-use-indicator">
            <span className="tool-name">{msg.toolUse.name}</span>
            <span className="tool-result">{msg.toolUse.result}</span>
          </div>
        )}
        {msg.planRef && <span className="plan-ref">Plan: {msg.planRef}</span>}
        <div className="chat-msg-time">{formatTime(msg.timestamp)}</div>
      </div>
    </div>
  );
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}
