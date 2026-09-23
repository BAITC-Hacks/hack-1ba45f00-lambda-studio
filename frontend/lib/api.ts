export type Role = 'consolidator' | 'transit' | 'distributor' | 'terminal' | 'coordinator' | 'peripheral';

export type GraphNode = {
  id: string;
  x: number;
  y: number;
  role: Role;
  role_score: number;
  cluster: number;
  priority: number;
  rank: number | null;
  is_seed: boolean;
  depth: number;
  truncated: boolean;
  in_core: boolean;
  in_kzt: number;
  out_kzt: number;
  evidence: string;
  fx?: number;
  fy?: number;
};

export type GraphLink = {
  source: string | GraphNode;
  target: string | GraphNode;
  sum_kzt: number;
  n_tx: number;
};

export type GraphPayload = { nodes: GraphNode[]; edges: GraphLink[] };
export type RoleMeta = { id: Role; label: string; color: string; count: number };
export type MetaPayload = {
  roles: RoleMeta[];
  network: { n_nodes: number; n_edges: number; n_seeds: number; total_kzt: number; n_clusters: number; core_size: number; period: string };
};
export type TopCheck = { rank: number; id: string; role: Role; priority: number; why: string };
export type FlowPeer = { id: string; sum_kzt: number; n_tx: number; role: Role };
export type NodeDetail = GraphNode & {
  role_label: string;
  stability_text: string;
  metrics: Record<string, number | null>;
  rule_trace: Array<{ role: string; matched: boolean; skipped?: boolean; checks: Array<{ label: string; value: number | string | null; ok: boolean }> }>;
  upstream_seeds: string[];
  payers: FlowPeer[];
  recipients: FlowPeer[];
};
export type BlockResult = {
  baseline: { reach_kzt: number; deep_kzt: number; n_components: number };
  after: { reach_kzt: number; deep_kzt: number; n_components: number };
  drop_reach_pct: number;
  drop_deep_pct: number;
  removed: string[];
};

export type Ego = { center: string; nodes: string[]; edges: GraphLink[] };
export type Health = { status: string; ai_enabled: boolean; generated_at: string };
export type Sankey = { nodes: { name: string }[]; links: { source: string; target: string; value: number }[]; skipped_kzt: number };
export type Strategy = BlockResult['after'] & { removed?: string[]; drop_reach_pct: number; drop_deep_pct: number };
export type Resilience = { baseline: BlockResult['baseline']; strategies: Record<string, Record<string, Strategy>> };
export type TopBlock = { step: number; id: string; role: Role; cut_kzt_marginal: number; reach_kzt_after: number; why: string };
export type Brief = { markdown: string; verified: boolean; issues: string[]; cached: boolean; generated_at: string };
export type Answer = { answer: string; verified: boolean; issues: string[]; gids: string[]; tool_calls: unknown[] };
export type DataMode = 'api' | 'snapshot';
let mode: DataMode = 'api';
export function useDataMode(value: DataMode) { mode = value; }
const configuredBase = process.env.NEXT_PUBLIC_API_BASE;
function base() { return (configuredBase ?? (process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : '')).replace(/\/$/, ''); }
export async function snapshot<T>(name: string): Promise<T> {
  const response = await fetch(`/snapshot/${name}.json`);
  if (!response.ok) throw new Error('Сохранённые данные недоступны. Запустите сервер и повторите.');
  return response.json();
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base()}${path}`, { ...init, signal: init?.signal ?? AbortSignal.timeout(path === '/api/ask' ? 120000 : 20000), headers: { 'Content-Type': 'application/json', ...init?.headers } });
  if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(error.message || `Ошибка сервера (${response.status}). Повторите запрос.`); }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>('/api/health'),
  graph: () => mode === 'snapshot' ? snapshot<GraphPayload>('graph') : request<GraphPayload>('/api/graph'),
  meta: () => mode === 'snapshot' ? snapshot<MetaPayload>('meta') : request<MetaPayload>('/api/meta'),
  topCheck: () => mode === 'snapshot' ? snapshot<TopCheck[]>('top_check') : request<TopCheck[]>('/api/top?kind=check&limit=30'),
  node: async (id: string) => {
    if (mode === 'api') return request<NodeDetail>(`/api/node/${encodeURIComponent(id)}`);
    const cards = await snapshot<Record<string, NodeDetail>>('cards');
    if (!cards[id]) throw new Error('Узел не найден в сохранённых данных.');
    return cards[id];
  },
  ego: (id: string) => request<Ego>(`/api/node/${encodeURIComponent(id)}/ego?hops=1`),
  search: (query: string) => request<Array<{ id: string; role: Role; priority: number }>>(`/api/search?q=${encodeURIComponent(query)}&limit=10`),
  block: (ids: string[]) => request<BlockResult>('/api/block', { method: 'POST', body: JSON.stringify({ ids }) }),
  sankey: () => mode === 'snapshot' ? snapshot<Sankey>('sankey') : request<Sankey>('/api/sankey'),
  resilience: () => mode === 'snapshot' ? snapshot<Resilience>('resilience') : request<Resilience>('/api/resilience'),
  topBlock: () => mode === 'snapshot' ? snapshot<TopBlock[]>('top_block') : request<TopBlock[]>('/api/top?kind=block&limit=20'),
  brief: () => mode === 'snapshot' ? snapshot<Brief>('brief') : request<Brief>('/api/brief'),
  ask: (question: string) => request<Answer>('/api/ask', { method: 'POST', body: JSON.stringify({ question }) }),
};
