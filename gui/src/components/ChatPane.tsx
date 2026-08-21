import { useState, useRef, useEffect } from "react";
import type { ChatResponse, ChatMessage } from "../api";

interface Props {
  messages: ChatMessage[];
  loading: boolean;
  labActive: boolean;
  onSend: (text: string) => void;
  onClear: () => void;
}

function ToolCallDetail({ tc }: { tc: ChatResponse["tool_calls"][number] }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="bg-white border border-gray-200 rounded px-2 py-1 text-xs">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 w-full text-left font-mono hover:text-blue-600"
      >
        <span className="text-gray-400 select-none">{open ? "▼" : "▶"}</span>
        <span className="font-semibold">{tc.tool}</span>
        {tc.args && (
          <span className="text-gray-500 ml-1">
            ({Object.entries(tc.args).map(([k, v]) => `${k}=${v}`).join(", ")})
          </span>
        )}
        {tc.error && <span className="text-red-500 ml-1">failed</span>}
      </button>
      {open && (
        <div className="mt-1 pl-4 font-mono text-gray-600 break-all border-t border-gray-100 pt-1">
          {tc.error && <p className="text-red-500">{tc.error}</p>}
          {!!tc.result && (
            <pre className="whitespace-pre-wrap text-xs">
              {typeof tc.result === "string" ? tc.result : JSON.stringify(tc.result, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

export default function ChatPane({ messages, loading, labActive, onSend, onClear }: Props) {
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function handleSend() {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    onSend(text);
  }

  function handleClear() {
    onClear();
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && (
          <p className="text-sm text-gray-400 mt-8 text-center">
            {labActive
              ? 'Try: "block ping from pc1 to pc2"'
              : "Start the lab first"}
          </p>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                msg.role === "user"
                  ? "bg-blue-600 text-white"
                  : "bg-gray-100 text-gray-800"
              }`}
            >
              {msg.error && (
                <p className="text-red-600 text-xs">{msg.error}</p>
              )}
              {msg.content && <p className="whitespace-pre-wrap">{msg.content}</p>}
              {msg.toolCalls && msg.toolCalls.length > 0 && (
                <div className="mt-2 space-y-1">
                  {msg.toolCalls.map((tc, j) => (
                    <ToolCallDetail key={j} tc={tc} />
                  ))}
                </div>
              )}
              {msg.metrics && (
                <p className="text-xs text-gray-400 mt-1">
                  LLM: {msg.metrics.llm_latency_s}s
                  {msg.metrics.total_latency_s && ` · Total: ${msg.metrics.total_latency_s}s`}
                </p>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-100 rounded-lg px-3 py-2 text-sm text-gray-500">
              Thinking...
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      <div className="border-t border-gray-200 p-3">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            placeholder={labActive ? "Ask the LLM..." : "Start the lab first"}
            disabled={!labActive || loading}
            className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-400"
          />
          <button
            onClick={handleSend}
            disabled={!labActive || loading || !input.trim()}
            className="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-40 hover:bg-blue-700 transition-colors"
          >
            Send
          </button>
          {messages.length > 0 && (
            <button
              onClick={handleClear}
              disabled={loading}
              className="text-gray-400 hover:text-gray-600 px-2 py-2 text-sm transition-colors"
              title="Clear chat"
            >
              Clear
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
