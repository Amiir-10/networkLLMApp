const BASE = "http://localhost:8000";
const WS_BASE = "ws://localhost:8000";

export interface HealthResponse {
  status: string;
  lab_active: boolean;
  scenario: string | null;
  firewall_connected: boolean;
  firewalls?: string[];
}

// ── Scenario graph (drives the data-driven topology) ──────────────────────
export interface ScenarioSummary {
  name: string;
  description: string;
}

export interface GraphInterface {
  to: string;
  ip: string | null;       // null for a switch's L2-only ports
  gateway: string | null;
}

export interface GraphNode {
  id: string;
  role: "pc" | "router" | "firewall" | "switch";
  image: string;
  interfaces: GraphInterface[];
  ports: number[];
}

export interface ScenarioGraph {
  name: string;
  description: string;
  nodes: GraphNode[];
}

export interface ToolCallResult {
  tool: string;
  args?: Record<string, string>;
  result?: unknown;
  error?: string;
}

// A ping_test result the topology view animates along the src->dst path.
export interface PingEvent {
  id: number;
  src: string;
  dst: string;
  blocked: boolean;
}

export interface ParsedRule {
  src_ip: string | null;
  dst_ip: string | null;
  proto: string | null;
  port: string | null;
  action: "drop" | "accept" | "reject";
  raw: string;
  firewall?: string | null;  // which firewall enforces it (multi-fw scenarios)
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

// A rendered chat message. Lives at App level (not inside ChatPane) so the
// conversation survives switching the console<->chat tabs within a session —
// ChatPane unmounts on tab change, App does not.
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  toolCalls?: ChatResponse["tool_calls"];
  metrics?: ChatResponse["metrics"];
  error?: string;
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch(`${BASE}/health`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchModels(): Promise<string[]> {
  const res = await fetch(`${BASE}/models`);
  if (!res.ok) throw new Error(await res.text());
  const data: { models: string[] } = await res.json();
  return data.models;
}

export async function fetchScenarios(): Promise<ScenarioSummary[]> {
  const res = await fetch(`${BASE}/scenarios`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchScenario(name: string): Promise<ScenarioGraph> {
  const res = await fetch(`${BASE}/scenarios/${name}`);
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

export interface AddRuleResponse {
  status: string;
  src: string;
  dst: string;
  proto: string;
  result: unknown;
}

// Add a firewall DROP rule via the console form. Hits POST /rules, which calls
// the SAME security.block(...) the LLM's block_traffic tool uses — so a rule
// added here is byte-identical to one the LLM adds, and both mirror into the
// `/` view (every view reads the one GET /rules source).
export async function addRule(
  src: string,
  dst: string,
  proto: string = "icmp",
  port: number | null = null,
  firewall: string | null = null
): Promise<AddRuleResponse> {
  const res = await fetch(`${BASE}/rules`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // port only matters for tcp/udp; send null otherwise (backend ignores it).
    // firewall null = backend resolves the target from the source subnet.
    body: JSON.stringify({ src, dst, proto, port, firewall }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// WebSocket URL for a real PTY shell into a lab container (GET /ws/console/...).
export function wsConsoleUrl(scenario: string, node: string): string {
  return `${WS_BASE}/ws/console/${scenario}/${node}`;
}
