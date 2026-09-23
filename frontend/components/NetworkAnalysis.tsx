'use client';

import dynamic from 'next/dynamic';
import FocusNetwork from './FocusNetwork';
import { AISection, BlockingSection, MoneyLadder } from './AnalysisSections';
import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import fallbackGraph from '../public/graph.json';
import fallbackMeta from '../public/meta.json';
import fallbackTop from '../public/top_check.json';
import { api, useDataMode, DataMode, Health, Ego, BlockResult, FlowPeer, GraphLink, GraphNode, GraphPayload, MetaPayload, NodeDetail, Role, TopCheck } from '../lib/api';

const ForceGraph2D = dynamic(() => import('react-force-graph-2d'), { ssr: false });
const layers = ['ПОВЕРХНОСТЬ · ИЗВЕСТНЫЕ КУРЬЕРЫ', 'КОЛЕНО 1', 'КОЛЕНО 2', 'КОЛЕНО 3', 'КОЛЕНО 4'];
const fallbackColors: Record<Role, string> = { consolidator: '#EDAE49', transit: '#3E8ED0', distributor: '#8E6BBF', terminal: '#4F9D69', coordinator: '#D1495B', peripheral: '#687078' };
const idOf = (value: string | GraphNode) => typeof value === 'string' ? value : value.id;
const TOP_NEIGHBOURS = 5;   // «Топ-30 + соседи»: сколько крупнейших соседей показывать у каждого узла топа
const money = (value: number | null | undefined) => value == null ? '—' : value >= 1e6 ? `${(value / 1e6).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} млн ₸` : `${Math.round(value / 1000).toLocaleString('ru-RU')} тыс. ₸`;
const pct = (value: number) => `${(value * 100).toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;

function localDetail(node: GraphNode, nodes: GraphNode[], edges: GraphLink[], labels: Record<Role, string>): NodeDetail {
  const peers = (direction: 'in' | 'out'): FlowPeer[] => edges.filter(edge => direction === 'in' ? idOf(edge.target) === node.id : idOf(edge.source) === node.id).map(edge => {
    const id = direction === 'in' ? idOf(edge.source) : idOf(edge.target);
    return { id, sum_kzt: edge.sum_kzt, n_tx: edge.n_tx, role: nodes.find(item => item.id === id)?.role ?? 'peripheral' };
  });
  const payers = peers('in'); const recipients = peers('out');
  return { ...node, role_label: labels[node.role], stability_text: 'Предварительная гипотеза по доступному графу', metrics: { in_deg: payers.length, out_deg: recipients.length, in_kzt: node.in_kzt, out_kzt: node.out_kzt }, rule_trace: [], upstream_seeds: [], payers, recipients };
}

export default function NetworkAnalysis() {
  const [graph, setGraph] = useState<GraphPayload>(fallbackGraph as GraphPayload);
  const [meta, setMeta] = useState<MetaPayload>(fallbackMeta as MetaPayload);
  const [top, setTop] = useState<TopCheck[]>(fallbackTop as TopCheck[]);
  const [source, setSource] = useState<DataMode>('snapshot');
  const [tab, setTab] = useState<Tab>('graph');
  const [health, setHealth] = useState<Health | null>(null);
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [detailError, setDetailError] = useState('');
  const [busy, setBusy] = useState(false);
  const [ego, setEgo] = useState<Ego | null>(null);
  const [coreOnly, setCoreOnly] = useState(false);
  const [colorMode, setColorMode] = useState('role');
  const [searchMessage, setSearchMessage] = useState('');
  const [overview, setOverview] = useState(false);
  const searchVersion = useRef(0);
  const [filtersOpen, setFiltersOpen] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const [role, setRole] = useState('all');
  const [cluster, setCluster] = useState('all');
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState<GraphNode[]>([]);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [blocked, setBlocked] = useState<Set<string>>(new Set());
  const [withered, setWithered] = useState<Set<string>>(new Set());
  const [blockResult, setBlockResult] = useState<BlockResult | null>(null);
  const graphRef = useRef<any>(null);
  const simulationTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const simulationVersion = useRef(0);
  const detailVersion = useRef(0);
  useEffect(() => () => simulationTimers.current.forEach(clearTimeout), []);
  const graphBox = useRef<HTMLElement | null>(null);
  const [size, setSize] = useState({ width: 900, height: 560 });

  useEffect(() => {
    let active = true;
    (async () => {
      let mode: DataMode = 'api';
      try { const h = await api.health(); if (active) setHealth(h); }
      catch { mode = 'snapshot'; if (active) setNotice('Сервер недоступен. Показаны сохранённые результаты; новое моделирование недоступно.'); }
      if (process.env.NEXT_PUBLIC_DATA_MODE === 'snapshot') mode = 'snapshot';
      useDataMode(mode);
      try {
        const [nextGraph, nextMeta, nextTop] = await Promise.all([api.graph(), api.meta(), api.topCheck()]);
        if (active) { setGraph(nextGraph); setMeta(nextMeta); setTop(nextTop); setSource(mode); }
      } catch (e) { if (active) setNotice(e instanceof Error ? e.message : 'Не удалось загрузить данные'); }
      finally { if (active) setLoading(false); }
    })();
    return () => { active = false; };
  }, []);

  const labels = useMemo(() => Object.fromEntries(meta.roles.map(item => [item.id, item.label.toUpperCase()])) as Record<Role, string>, [meta]);
  const colors = useMemo(() => ({ ...fallbackColors, ...Object.fromEntries(meta.roles.map(item => [item.id, item.color])) }) as Record<Role, string>, [meta]);
  const nodeById = useMemo(() => new Map(graph.nodes.map(node => [node.id, node])), [graph.nodes]);
  const showcase = useMemo(() => {
    const targets = top.slice(0, 3).map(item => item.id).filter(id => nodeById.has(id));
    const edges: GraphLink[] = []; const mids = new Set<string>(); const seeds = new Set<string>(); const positions = new Map<string, { x: number; y: number }>();
    const targetXs = [-270, 0, 270];
    const seedPaths = (target: string) => {
      const found: Array<{ edges: GraphLink[]; score: number; seed: string }> = [];
      const walk = (current: string, path: GraphLink[], visited: Set<string>, level: number, score: number) => {
        if (level >= 4 || found.length > 80) return;
        graph.edges.filter(edge => idOf(edge.target) === current).sort((a, b) => b.sum_kzt - a.sum_kzt).slice(0, 14).forEach(edge => {
          const source = idOf(edge.source); if (visited.has(source)) return;
          const nextPath = [edge, ...path]; const nextScore = Math.min(score, edge.sum_kzt);
          if (nodeById.get(source)?.is_seed) found.push({ edges: nextPath, score: nextScore, seed: source });
          else walk(source, nextPath, new Set([...visited, source]), level + 1, nextScore);
        });
      };
      walk(target, [], new Set([target]), 0, Number.POSITIVE_INFINITY);
      const unique = new Map<string, { edges: GraphLink[]; score: number; seed: string }>();
      found.sort((a, b) => b.score - a.score).forEach(path => { if (!unique.has(path.seed)) unique.set(path.seed, path); });
      return [...unique.values()].slice(0, 3);
    };
    targets.forEach((target, targetIndex) => {
      positions.set(target, { x: targetXs[targetIndex], y: 150 });
      seedPaths(target).forEach((path, pathIndex, paths) => {
        path.edges.forEach(edge => { if (!edges.some(item => idOf(item.source) === idOf(edge.source) && idOf(item.target) === idOf(edge.target))) edges.push(edge); });
        seeds.add(path.seed); const seedX = targetXs[targetIndex] + (pathIndex - (paths.length - 1) / 2) * 74;
        positions.set(path.seed, { x: seedX, y: -205 });
        const pathNodes = path.edges.map(edge => idOf(edge.target)).filter(id => id !== target);
        pathNodes.forEach((id, index) => { mids.add(id); if (!positions.has(id)) { const progress = (index + 1) / (pathNodes.length + 1); positions.set(id, { x: seedX + (targetXs[targetIndex] - seedX) * progress, y: -205 + 355 * progress }); } });
      });
    });
    return { targets: new Set(targets), mids, seeds, positions, edges };
  }, [graph.edges, nodeById, top]);
  const compactIds = useMemo(() => new Set(showcase.positions.keys()), [showcase]);
  const priorityIds = useMemo(() => {
    // Топ-30 и у каждого — до 5 крупнейших по сумме соседей: у распределителей по 60–116 получателей,
    // и «все соседи» превращают вид в клубок (~600 узлов вместо ~150).
    const primary = new Set(top.map(n => n.id)), ids = new Set(primary);
    const byNode = new Map<string, { id: string; sum: number }[]>();
    graph.edges.forEach(e => {
      const s = idOf(e.source), t = idOf(e.target);
      if (primary.has(s)) byNode.set(s, [...(byNode.get(s) ?? []), { id: t, sum: e.sum_kzt }]);
      if (primary.has(t)) byNode.set(t, [...(byNode.get(t) ?? []), { id: s, sum: e.sum_kzt }]);
    });
    byNode.forEach(list => list.sort((a, b) => b.sum - a.sum).slice(0, TOP_NEIGHBOURS).forEach(n => ids.add(n.id)));
    return ids;
  }, [graph.edges, top]);
  const shown = useMemo(() => graph.nodes.filter(node => (showAll ? (!ego || ego.nodes.includes(node.id) || !!detail?.upstream_seeds.includes(node.id)) : overview ? priorityIds.has(node.id) : compactIds.has(node.id)) && (!coreOnly || node.in_core) && (role === 'all' || node.role === role) && (cluster === 'all' || node.cluster === Number(cluster))).map(node => {
    const position = showcase.positions.get(node.id);
    return (showAll || overview) ? { ...node, fx: node.x, fy: node.y } : { ...node, x: position?.x ?? 0, fx: position?.x ?? 0, fy: position?.y ?? 0 };
  }), [graph.nodes, showAll, compactIds, role, cluster, showcase, ego, detail, coreOnly, overview, priorityIds]);
  const shownIds = useMemo(() => new Set(shown.map(node => node.id)), [shown]);
  const shownLinks = useMemo(() => ((showAll || overview) ? graph.edges : showcase.edges).filter(edge => shownIds.has(idOf(edge.source)) && shownIds.has(idOf(edge.target))).map(edge => ({ ...edge })), [graph.edges, shownIds, showAll, showcase.edges, overview]);
  const useGraphData = useMemo(() => ({nodes:shown,links:shownLinks}), [shown,shownLinks]);
  const selectedLinks = useMemo(() => selected ? new Set(graph.edges.filter(edge => idOf(edge.source) === selected.id || idOf(edge.target) === selected.id).map(edge => `${idOf(edge.source)}>${idOf(edge.target)}`)) : new Set<string>(), [graph.edges, selected]);
  const clusters = useMemo(() => [...new Set(graph.nodes.map(node => node.cluster))].sort((a, b) => a - b), [graph.nodes]);

  useEffect(() => {
    const element = graphBox.current; if (!element) return;
    const update = () => setSize({ width: element.clientWidth, height: element.clientHeight });
    update(); const observer = new ResizeObserver(update); observer.observe(element); return () => observer.disconnect();
  }, [tab, selected]);
  const fitGraph = useCallback(() => {
    const visible = selected && ego
      ? shown.filter(n => n.id === selected.id || ego.edges.some(e=>idOf(e.source)===n.id||idOf(e.target)===n.id))
      : shown;
    if(!visible.length || !graphRef.current)return;
    const xs=visible.map(n=>n.fx ?? n.x), ys=visible.map(n=>n.fy ?? n.y);
    const left=Math.min(...xs), right=Math.max(...xs), topY=Math.min(...ys), bottom=Math.max(...ys);
    graphRef.current.centerAt((left+right)/2,(topY+bottom)/2,350);
    graphRef.current.zoom(Math.max(.05,Math.min(2.5,(size.width-160)/Math.max(80,right-left),(size.height-180)/Math.max(80,bottom-topY))),350);
  }, [shown,selected,ego,size.width,size.height]);
  useEffect(() => {
    if (tab !== 'graph' || !shown.length) return;
    const timer = setTimeout(fitGraph,250);
    return () => clearTimeout(timer);
  }, [tab,showAll,overview,fitGraph]);

  const focus = useCallback(async (node: GraphNode) => {
    const version = ++detailVersion.current;
    setRole('all'); setCluster('all'); setCoreOnly(false); setDetailError('');
    setSelected(node); setDetail(null); setSearchResults([]); setSearchMessage(''); setTab('graph');
    // All GIDs, including those outside the presentation subset, open with their real neighbourhood.
    const localEdges = graph.edges.filter(e => idOf(e.source) === node.id || idOf(e.target) === node.id);
    const localEgo = { center: node.id, nodes: [...new Set([node.id, ...localEdges.flatMap(e => [idOf(e.source), idOf(e.target)])])], edges: localEdges };
    setEgo(localEgo);
    if (!compactIds.has(node.id) || overview || showAll) setShowAll(true);
    try {
      const [card, near] = await Promise.all([api.node(node.id), source === 'api' ? api.ego(node.id) : Promise.resolve(localEgo)]);
      if (version === detailVersion.current) { setDetail(card); setEgo(near); }
    } catch (e) { if (version === detailVersion.current) setDetailError(e instanceof Error ? e.message : 'Карточка недоступна'); }
  }, [compactIds, graph.edges, overview, showAll, source]);

  const search = async (event: FormEvent) => {
    event.preventDefault(); const value = query.trim(); if (!value) return;
    const version = ++searchVersion.current;
    setSearchMessage('Ищем…');
    try {
      const matches = source === 'api'
        ? (await api.search(value)).map(item => nodeById.get(item.id)).filter(Boolean) as GraphNode[]
        : graph.nodes.filter(node => node.id.includes(value)).sort((a,b) => b.priority-a.priority).slice(0,10);
      if(version !== searchVersion.current) return;
      setSearchResults(matches); setSearchMessage(matches.length ? '' : 'GID не найден. Проверьте номер или введите его часть.');
      const exact = matches.find(n => n.id === value);
      if(exact || matches.length === 1) focus(exact ?? matches[0]);
    } catch(e) { if(version === searchVersion.current) setSearchMessage(e instanceof Error ? e.message : 'Поиск недоступен'); }
  };

  const applyBlock = async (ids: string[]) => {
    if (busy) return;
    if (source !== 'api') throw new Error('Для моделирования нужен работающий сервер.');
    const version = ++simulationVersion.current;
    setBusy(true); setNotice('');
    try {
      const result = await api.block(ids);
      if(version !== simulationVersion.current) return;
      simulationTimers.current.forEach(clearTimeout); simulationTimers.current = [];
      setBlocked(new Set(result.removed)); setWithered(new Set()); setBlockResult(result); setTab('graph');
      const seen = new Set(result.removed); let frontier = result.removed; let level = 0;
      while(frontier.length) {
        const next: string[] = [];
        for(const target of frontier) for(const edge of graph.edges) if(idOf(edge.target) === target && !seen.has(idOf(edge.source))) { seen.add(idOf(edge.source)); next.push(idOf(edge.source)); }
        const wave = [...next];
        simulationTimers.current.push(setTimeout(() => setWithered(previous => new Set([...previous, ...wave])), ++level * 150));
        frontier = next;
      }
    } finally { if(version === simulationVersion.current) setBusy(false); }
  };
  const simulate = async () => {
    if (!selected || blocked.has(selected.id)) return;
    try { await applyBlock([...new Set([...blocked, selected.id])]); }
    catch(e) { setNotice(e instanceof Error ? e.message : 'Расчёт не выполнен'); }
  };

  const reset = () => { simulationVersion.current++; simulationTimers.current.forEach(clearTimeout); simulationTimers.current = []; setBlocked(new Set()); setWithered(new Set()); setBlockResult(null); setBusy(false); };
  const seedLoss = graph.nodes.filter(node => node.is_seed && withered.has(node.id)).length;

  const paintNode = (raw: any, context: CanvasRenderingContext2D, scale: number) => {
    const node = raw as GraphNode; const x = node.x ?? 0; const y = node.y ?? 0; const dead = blocked.has(node.id) || withered.has(node.id); const core = node.in_core || ['coordinator', 'consolidator'].includes(node.role);
    const showcaseCore = !showAll && showcase.targets.has(node.id); const showcaseMid = !showAll && showcase.mids.has(node.id);
    const radius = showcaseCore ? 17 + node.priority * 9 : showcaseMid ? 7 : node.is_seed ? 3 : core ? 5 + node.priority * 9 : Math.max(2, 2 + node.priority * 3); const color = dead ? '#3c4240' : node.is_seed ? '#d9dfdc' : colorMode === 'cluster' ? `hsl(${(node.cluster * 137.5) % 360} 45% 65%)` : colors[node.role];
    context.save(); if (core && !dead) { const glow = context.createRadialGradient(x, y, radius * .2, x, y, radius * 2.8); glow.addColorStop(0, color); glow.addColorStop(1, 'transparent'); context.fillStyle = glow; context.fillRect(x - radius * 3, y - radius * 3, radius * 6, radius * 6); }
    context.beginPath(); if (node.is_seed) { context.moveTo(x, y - radius); context.lineTo(x + radius, y); context.lineTo(x, y + radius); context.lineTo(x - radius, y); context.closePath(); } else context.arc(x, y, radius, 0, Math.PI * 2);
    context.fillStyle = color; context.globalAlpha = node.role === 'peripheral' && selected?.id !== node.id && !selectedLinks.size ? .3 : .92; context.fill(); context.globalAlpha = 1;
    if (showcaseCore || showcaseMid) { context.beginPath(); context.arc(x, y, radius + (showcaseCore ? 7 : 4) / scale, 0, Math.PI * 2); context.strokeStyle = `${color}a8`; context.lineWidth = (showcaseCore ? 1.4 : 1) / scale; context.stroke(); context.beginPath(); context.arc(x, y, radius * .42, 0, Math.PI * 2); context.fillStyle = '#e8f5dc'; context.globalAlpha = .82; context.fill(); context.globalAlpha = 1; }
    if (node.in_core || detail?.upstream_seeds.includes(node.id)) { context.beginPath(); context.arc(x,y,radius+4/scale,0,Math.PI*2); context.strokeStyle=detail?.upstream_seeds.includes(node.id)?'#fff':'#a3e635'; context.lineWidth=1/scale; context.stroke(); }
    if (node.truncated) { context.setLineDash([2 / scale, 2 / scale]); context.strokeStyle = '#d8dfdc'; context.lineWidth = .8 / scale; context.stroke(); }
    if (selected?.id === node.id) { context.beginPath(); context.arc(x, y, radius + 6 / scale, 0, Math.PI * 2); context.strokeStyle = '#fff'; context.lineWidth = 1.2 / scale; context.stroke(); }
    if ((selected?.id === node.id || showcaseCore || (showAll && node.priority >= .9)) && scale > .8) { context.fillStyle = '#dfe6e2'; context.font = `${9 / scale}px monospace`; context.textAlign = 'center'; context.fillText(`GID ·${node.id.slice(-6)}`, x, y + radius + 14 / scale); if (showcaseCore) { context.fillStyle = '#89968c'; context.font = `${7 / scale}px sans-serif`; context.fillText(labels[node.role], x, y + radius + 26 / scale); } }
    context.restore();
  };
  const paintLayers = (context: CanvasRenderingContext2D, scale: number) => { context.save(); context.font = `${8 / scale}px monospace`; const rows = showAll ? layers.map((name, depth) => ({ name, y: depth * 118 - 236 })) : [{ name: 'ПОВЕРХНОСТЬ · ИЗВЕСТНЫЕ КУРЬЕРЫ', y: -205 }, { name: 'ТРАНЗИТНЫЙ СЛОЙ', y: -8 }, { name: 'ПРИОРИТЕТНЫЕ УЗЛЫ · РЕКОМЕНДУЕМ ПРОВЕРИТЬ', y: 150 }]; rows.forEach(row => { context.beginPath(); context.setLineDash([4 / scale, 8 / scale]); context.moveTo(-900, row.y); context.lineTo(900, row.y); context.strokeStyle = 'rgba(163,230,53,.11)'; context.lineWidth = .7 / scale; context.stroke(); context.fillStyle = 'rgba(160,170,163,.7)'; context.fillText(row.name, -315, row.y - 7 / scale); }); context.restore(); };

  return <main className="screen"><Header tab={tab} setTab={setTab} /><Kpis meta={meta} /><div className="page-intro"><div><span className="eyebrow">MYCELIUM / AML NETWORK</span><h1>Сеть под поверхностью</h1><p>От известных курьеров — к узлам, где сходятся денежные потоки.</p></div><span className={`demo-tag ${source === 'api' ? 'live' : ''}`}>● {loading ? 'Подключение…' : source === 'api' ? 'Данные сервера' : 'Сохранённые данные'}</span></div><div className={`workspace ${filtersOpen ? '' : 'filters-hidden'}`}>
    {notice && <div className="app-notice" role="status">{notice}<button aria-label="Закрыть уведомление" onClick={()=>setNotice('')}>×</button></div>}
    {tab === 'graph' ? <><aside className={`filters hud ${filtersOpen ? 'open' : 'closed'}`}><button className="collapse" onClick={() => setFiltersOpen(value => !value)}>{filtersOpen ? '‹' : '›'}</button>{filtersOpen && <><Label>ФИЛЬТРЫ СЕТИ</Label><Field label="ПРИЗНАКИ РОЛИ" value={role} set={v=>{setRole(v);setShowAll(true);setEgo(null);}} options={meta.roles.map(item => [item.id, item.label.toUpperCase()])} /><Field label="КЛАСТЕР" value={cluster} set={v=>{setCluster(v);setShowAll(true);setEgo(null);}} options={clusters.map(value => [String(value), `КЛАСТЕР ${value}`])} /><div className="view-controls"><button onClick={()=>{setShowAll(false);setOverview(false);setCoreOnly(false);setEgo(null);setSelected(null);setDetail(null);}}>Обзор путей</button><button onClick={()=>{setOverview(true);setShowAll(false);setCoreOnly(false);setEgo(null);setSelected(null);setDetail(null);}}>Топ-30 + соседи</button><button onClick={()=>{setShowAll(true);setCoreOnly(false);setEgo(null);setSelected(null);setDetail(null);}}>Весь граф</button><button onClick={()=>{setShowAll(true);setEgo(null);setCoreOnly(v=>!v);}}>Ядро {coreOnly?'✓':''}</button></div><Field label="ЦВЕТ В ПОЛНОМ ГРАФЕ" value={colorMode} set={setColorMode} allowAll={false} options={ [['role','По роли'],['cluster','По кластеру']] }/><div className="rule" /><Label>РЕКОМЕНДУЕМ ПРОВЕРИТЬ</Label><div className="core-picks">{top.slice(0, 20).map(item => <button key={item.id} onClick={() => nodeById.get(item.id) && focus(nodeById.get(item.id)!)} className={selected?.id === item.id ? 'chosen' : ''}><span><b>#{item.rank} · GID {item.id.slice(-8)}</b><em>{labels[item.role]}</em></span><small>{pct(item.priority)}</small></button>)}</div><div className="rule" /><Label>ЛЕГЕНДА РОЛЕЙ</Label>{meta.roles.map(item => <div className="legend" key={item.id}><i style={{ background: colors[item.id] }} />{item.label.toUpperCase()} <small>{item.count}</small></div>)}</>}</aside>
      <section ref={graphBox} className="graph hud"><div className="toolbar"><div className="search-wrap"><form onSubmit={search}><input value={query} onChange={event => setQuery(event.target.value)} placeholder="GID / ПОИСК" /><button>НАЙТИ</button></form>{searchResults.length > 1 && <div className="search-results">{searchResults.map(node => <button key={node.id} onClick={() => focus(node)}><span>{node.id}</span><small>{labels[node.role]} · {pct(node.priority)}</small></button>)}</div>}</div><span>{showAll ? ego ? 'ОКРЕСТНОСТЬ УЗЛА' : 'ПОЛНЫЙ ГРАФ' : overview ? 'ТОП-30 + СОСЕДИ' : 'ФОКУС · 3 ПРИОРИТЕТНЫХ УЗЛА'} · {shown.length} УЗЛОВ</span></div>
        {searchMessage && <div className="search-message" role="status">{searchMessage}</div>}
        {!shown.length && <div className="section-state">По выбранным фильтрам узлов нет.</div>}
        {!showAll && !overview ? <FocusNetwork nodes={shown} links={shownLinks} targets={showcase.targets} selected={selected?.id} dead={new Set([...blocked, ...withered])} onSelect={focus} /> : <>
        <ForceGraph2D ref={graphRef} width={size.width} height={size.height} graphData={useGraphData} backgroundColor="rgba(0,0,0,0)" nodeCanvasObject={paintNode} nodePointerAreaPaint={(node: any, color: string, context: CanvasRenderingContext2D) => { context.beginPath(); context.arc(node.x, node.y, 12, 0, Math.PI * 2); context.fillStyle = color; context.fill(); }} enableNodeDrag={false} nodeLabel={(n:any)=>`GID ${n.id} · ${labels[n.role as Role]} · колено ${n.depth}`} cooldownTicks={1} onEngineStop={fitGraph} d3AlphaDecay={.05} linkCurvature={showAll ? .08 : .16} linkColor={(edge: any) => { const key = `${idOf(edge.source)}>${idOf(edge.target)}`; if (withered.has(idOf(edge.source)) || withered.has(idOf(edge.target))) return 'rgba(70,76,74,.06)'; return selectedLinks.has(key) ? 'rgba(237,174,73,.85)' : showAll ? 'rgba(124,242,192,.18)' : 'rgba(151,221,172,.55)'; }} linkWidth={(edge: any) => selectedLinks.has(`${idOf(edge.source)}>${idOf(edge.target)}`) ? 2.4 : showAll ? Math.min(1.8, .25 + Math.log10(Math.max(edge.sum_kzt, 1)) / 7) : Math.min(2.4, .7 + Math.log10(Math.max(edge.sum_kzt, 1)) / 5)} linkDirectionalArrowLength={showAll ? 4 : 6} linkDirectionalArrowRelPos={.97} linkDirectionalParticles={(edge: any) => withered.has(idOf(edge.source)) || withered.has(idOf(edge.target)) ? 0 : selectedLinks.has(`${idOf(edge.source)}>${idOf(edge.target)}`) ? 3 : 1} linkDirectionalParticleWidth={(edge: any) => selectedLinks.has(`${idOf(edge.source)}>${idOf(edge.target)}`) ? 2.2 : 1.2} linkDirectionalParticleSpeed={.0025} linkDirectionalParticleColor={() => '#7CF2C0'} onNodeClick={(node: any) => focus(node)} />
        </>}
        <div className="graph-caption"><span>◇ Источники на поверхности</span><span>↓ Направление движения денег</span><span>◉ Крупнее узел — выше приоритет проверки</span></div>{blocked.size > 0 && <div className="simulation-banner"><strong>−{blockResult?.drop_reach_pct.toLocaleString('ru-RU')}%</strong><span>поток денег курьеров <small>Фрагментов сети: {blockResult?.after.n_components}. Подсветка показывает входящие пути, не число отключённых курьеров.</small>{blockResult && <small>Охват потока −{blockResult.drop_reach_pct.toLocaleString('ru-RU')}% · глубокий поток −{blockResult.drop_deep_pct.toLocaleString('ru-RU')}%</small>}</span><button onClick={reset}>Сбросить</button></div>}
      </section>{selected && !detail && <aside className="drawer hud"><button className="x" onClick={()=>{detailVersion.current++;setSelected(null);}}>×</button><p role="status">{detailError||'Загружаем карточку…'}</p>{detailError&&<button onClick={()=>focus(selected)}>Повторить</button>}</aside>}{selected && detail && <NodeDrawer detail={detail} colors={colors} labels={labels} simulate={simulate} reset={reset} isBlocked={blocked.has(selected.id)} unavailable={loading || source !== 'api' || busy} anyBlocked={blocked.size > 0} close={() => { detailVersion.current++; setSelected(null); setDetail(null); setEgo(null); }} focusId={id => nodeById.get(id) && focus(nodeById.get(id)!)} />}</> : tab === 'sankey' ? <MoneyLadder key={source}/> : tab === 'ai' ? <AISection key={source} mode={source} health={health} focus={id=>{const n=nodeById.get(id);if(n)focus(n);}}/> : tab === 'block' ? <BlockingSection key={source} mode={source} busy={busy} apply={applyBlock} focus={id=>{const n=nodeById.get(id);if(n)focus(n);}}/> : <TopList rows={top} colors={colors} labels={labels} focus={id => nodeById.get(id) && focus(nodeById.get(id)!)} />}
  </div></main>;
}

type Tab = 'graph' | 'top' | 'sankey' | 'block' | 'ai';
function Header({tab,setTab}:{tab:Tab;setTab:(value:Tab)=>void}) { return <header className="header hud"><div className="brand"><Logo/><div><b>Грибница</b><small>Анализ сети переводов</small></div></div><nav>{([['graph','Анализ сети'],['top','Кого проверять'],['sankey','Лестница денег'],['block','Блокировка'],['ai','Справка ИИ']] as [Tab,string][]).map(([id,label])=><button key={id} className={tab===id?'active':''} onClick={()=>setTab(id)}>{label}</button>)}</nav><span className="status">● Гипотезы для проверки</span></header>; }
function Logo() { return <svg viewBox="0 0 38 32"><path d="M3 6h11l5 7 7-4 9 7M7 27l8-8 7 5 11-4" /><circle cx="3" cy="6" r="2" /><circle cx="19" cy="13" r="2" /><circle cx="35" cy="16" r="2" /><circle cx="7" cy="27" r="2" /><circle cx="22" cy="24" r="2" /></svg>; }
function Kpis({ meta }: { meta: MetaPayload }) { const network = meta.network; return <div className="kpis"><Kpi value={network.n_nodes} label="УЗЛОВ В СЕТИ" /><Kpi value={network.n_edges} label="СВЯЗЕЙ" /><Kpi value={money(network.total_kzt)} label="ОБОРОТ" /><Kpi value={network.n_seeds} label="ИЗВЕСТНЫХ КУРЬЕРОВ" /><Kpi value={network.core_size} label="УЗЛОВ ЯДРА" /></div>; }
function Kpi({ value, label }: { value: string | number; label: string }) { return <div><b>{value}</b><span>{label}</span></div>; }
function Label({ children }: { children: ReactNode }) { return <p className="label">{children}</p>; }
function Field({ label, value, set, options, allowAll = true }: { label: string; value: string; set: (value: string) => void; options: string[][]; allowAll?:boolean }) { return <label className="field"><span>{label}</span><select value={value} onChange={event => set(event.target.value)}>{allowAll && <option value="all">ВСЕ</option>}{options.map(([option, text]) => <option value={option} key={option}>{text}</option>)}</select></label>; }
function Badge({ role, colors, labels }: { role: Role; colors: Record<Role, string>; labels: Record<Role, string> }) { return <span className="badge" style={{ color: colors[role], borderColor: `${colors[role]}55` }}>{labels[role]}</span>; }
function NodeDrawer({ detail, colors, labels, simulate, reset, isBlocked, unavailable, anyBlocked, close, focusId }: { detail: NodeDetail; colors: Record<Role, string>; labels: Record<Role, string>; simulate: () => void; reset: () => void; isBlocked: boolean; unavailable:boolean; anyBlocked: boolean; close: () => void; focusId: (id: string) => void }) {
  const metricLabels:Record<string,string>={in_deg:'Плательщиков',out_deg:'Получателей',pass_through:'Коэффициент пропуска',hold_median_days:'Удержание, дней',seed_reach:'Связанных курьеров',cut_kzt:'Зависимый поток, ₸',sync_max_payers:'Синхронных плательщиков'};
  return <aside className="drawer hud"><button className="x" aria-label="Закрыть карточку" onClick={close}>×</button><Label>КАРТОЧКА УЗЛА · ГИПОТЕЗА</Label><div className="node-title"><b>{detail.id}</b><Badge role={detail.role} colors={colors} labels={labels}/></div><p className="muted">УСТОЙЧИВОСТЬ РОЛИ · {pct(detail.role_score)}</p><div className="meter"><i style={{width:`${detail.role_score*100}%`}}/></div><p className="stability">{detail.stability_text}</p><div className="evidence"><Label>ПРИЗНАКИ РОЛИ</Label>{detail.evidence}</div>{detail.truncated&&<p className="analysis-note">Граница выборки: исходящие переводы не собраны.</p>}<div className="node-volume"><span>Входящий поток<strong>{money(detail.metrics.in_kzt)}</strong></span><span>Исходящий поток<strong>{money(detail.metrics.out_kzt)}</strong></span></div><dl className="metric-list">{Object.entries(metricLabels).map(([key,label])=><div key={key}><dt>{label}</dt><dd title={detail.metrics[key]==null?'Метрика недоступна; у курьеров входящие занижены выгрузкой':undefined}>{detail.metrics[key]==null?'—':detail.metrics[key]?.toLocaleString('ru-RU',{maximumFractionDigits:2})}</dd></div>)}</dl><div className="rule-trace"><Label>ПОЧЕМУ ЭТА РОЛЬ</Label>{detail.rule_trace.map((rule,i)=><div key={i}><strong>{labels[rule.role as Role]||rule.role} {rule.matched?'✓':rule.skipped?'—':'✗'}</strong>{rule.skipped?<p>Не проверялось: сработало предыдущее правило.</p>:rule.checks.map((check,j)=><p key={j}><i>{check.ok?'✓':'✗'}</i>{check.label}: <b title={check.value==null?'Метрика недоступна':undefined}>{check.value??'—'}</b></p>)}</div>)}</div><div className="simulation-controls"><button className="simulate" disabled={isBlocked||unavailable} onClick={simulate}>{isBlocked?'Узел отключён в модели':'Смоделировать блокировку'}</button>{detail.is_seed&&<small>Известный курьер. Сервер объяснит ограничения моделирования для seed.</small>}{anyBlocked&&<button className="reset" onClick={reset}>Сбросить моделирование</button>}<small>Проверять и блокировать — разные задачи.</small></div><details><summary>Курьеры выше по цепочке · {detail.upstream_seeds.length}</summary>{detail.upstream_seeds.map(id=><button className="text-link peer-link" key={id} onClick={()=>focusId(id)}>{id}</button>)}</details><Flows title="ПОЛУЧАЛ ОТ" rows={detail.payers} focusId={focusId}/><Flows title="ОТДАВАЛ" rows={detail.recipients} focusId={focusId}/></aside>;
}
function Flows({ title, rows, focusId }: { title: string; rows: FlowPeer[]; focusId: (id: string) => void }) { return <div className="flows"><Label>{title} · {rows.length}</Label>{rows.map(row => <button title={row.id} onClick={() => focusId(row.id)} key={row.id}><span>·{row.id.slice(-8)} <small>{row.n_tx} тр.</small></span><em>{money(row.sum_kzt)}</em></button>)}</div>; }
function TopList({ rows, colors, labels, focus }: { rows: TopCheck[]; colors: Record<Role, string>; labels: Record<Role, string>; focus: (id: string) => void }) { return <section className="top hud"><Label>PRIORITY QUEUE / HYPOTHESES</Label><h1>Рекомендуем проверить</h1><table><thead><tr><th>РАНГ</th><th>GID</th><th>ПРИЗНАКИ РОЛИ</th><th>ПРИОРИТЕТ</th><th>ОБОСНОВАНИЕ</th></tr></thead><tbody>{rows.map(row => <tr key={row.id} onClick={() => focus(row.id)}><td>#{row.rank}</td><td>{row.id}</td><td><Badge role={row.role} colors={colors} labels={labels} /></td><td><div className="bar"><i style={{ width: `${row.priority * 100}%` }} /></div>{pct(row.priority)}</td><td>{row.why}</td></tr>)}</tbody></table></section>; }
