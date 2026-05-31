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

export interface ParsedRule {
  src_ip: string | null;
  dst_ip: string | null;
  proto: string | null;
  port: string | null;
  action: "drop" | "accept" | "reject";
  raw: string;
}

export interface RulesResponse {
  forward_rules: string[];
  zone_rules: string[];
  parsed: ParsedRule[];
}

export interface PingTestResult {
  ping_from: string;
  ping_to: string;
  loss_line: string;
  raw: { stdout?: string; stderr?: string; exit?: number };
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

export async function resetLab(scenario: string): Promise<unknown> {
  const res = await fetch(`${BASE}/lab/reset/${scenario}`, { method: "POST" });
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

export async function fetchRules(): Promise<RulesResponse> {
  const res = await fetch(`${BASE}/rules`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
