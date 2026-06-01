import TopologyPane from "./TopologyPane";
import ChatPane from "./ChatPane";
import type { BuiltTopology } from "../topology";
import type { ParsedRule, PingEvent, ToolCallResult } from "../api";

interface Props {
  topology: BuiltTopology | null;
  labReady: boolean;
  dropRules: ParsedRule[];
  pingEvent: PingEvent | null;
  onPingEventComplete: () => void;
  model: string;
  setModel: (m: string) => void;
  models: string[];
  chatResetKey: number;
  onChatComplete: (toolCalls: ToolCallResult[]) => void;
}

// The chat workspace: live topology on the left, LLM chat on the right.
export default function ChatView({
  topology,
  labReady,
  dropRules,
  pingEvent,
  onPingEventComplete,
  model,
  setModel,
  models,
  chatResetKey,
  onChatComplete,
}: Props) {
  return (
    <div className="flex-1 flex min-h-0">
      <div className="w-1/2 border-r border-gray-200 flex flex-col min-h-0">
        <div className="px-4 py-2 border-b border-gray-200">
          <h2 className="text-sm font-semibold text-gray-700">Network Topology</h2>
        </div>
        <div className="flex-1 min-h-0">
          <TopologyPane
            topology={topology}
            labReady={labReady}
            dropRules={dropRules}
            pingEvent={pingEvent}
            onPingEventComplete={onPingEventComplete}
          />
        </div>
      </div>

      <div className="w-1/2 flex flex-col min-h-0">
        <div className="px-4 py-2 border-b border-gray-200 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-700">Chat</h2>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="text-xs border border-gray-300 rounded px-2 py-1 bg-white"
          >
            {models.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </div>
        <div className="flex-1 min-h-0">
          <ChatPane key={chatResetKey} model={model} labActive={labReady} onChatComplete={onChatComplete} />
        </div>
      </div>
    </div>
  );
}
