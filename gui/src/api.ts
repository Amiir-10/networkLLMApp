const BASE = "http://localhost:8000";

export interface HealthResponse {
  status: string;
  lab_active: boolean;
  firewall_connected: boolean;
}

export interface ToolCallResult {
  tool: string;
  args?: Record<string, string>;
  result?: unknown;
  error?: string;
}

export interface ChatResponse {
  response: string;
  tool_calls: ToolCallResult[];
  metrics: {
    llm_latency_s: number;
    total_latency_s?: number;
  };
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch(`${BASE}/health`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function startLab(scenario: string): Promise<unknown> {
  const res = await fetch(`${BASE}/lab/start/${scenario}`, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function stopLab(scenario: string): Promise<unknown> {
  const res = await fetch(`${BASE}/lab/stop/${scenario}`, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function sendChat(message: string, model: string): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, model }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function clearChat(): Promise<void> {
  const res = await fetch(`${BASE}/chat/reset`, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
}
