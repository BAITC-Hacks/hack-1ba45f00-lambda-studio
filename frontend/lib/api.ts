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
  metrics: { in_deg: number; out_deg: number; in_kzt: number; out_kzt: number; pass_through?: number; hold_median_days?: number; seed_reach?: number; cut_kzt?: number };
  rule_trace: Array<{ role: string; matched: boolean; checks: Array<{ label: string; value: number | string; ok: boolean }> }>;
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

const base = (process.env.NEXT_PUBLIC_API_BASE ?? '').replace(/\/$/, '');

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json() as Promise<T>;
}

export const api = {
  graph: () => request<GraphPayload>('/api/graph'),
  meta: () => request<MetaPayload>('/api/meta'),
  topCheck: () => request<TopCheck[]>('/api/top?kind=check&limit=30'),
  node: (id: string) => request<NodeDetail>(`/api/node/${encodeURIComponent(id)}`),
  search: (query: string) => request<Array<{ id: string; role: Role; priority: number }>>(`/api/search?q=${encodeURIComponent(query)}&limit=10`),
  block: (ids: string[]) => request<BlockResult>('/api/block', { method: 'POST', body: JSON.stringify({ ids }) }),
};
