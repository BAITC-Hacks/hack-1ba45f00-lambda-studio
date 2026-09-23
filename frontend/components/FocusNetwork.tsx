'use client';

import { GraphNode, GraphLink } from '../lib/api';

type Props = { nodes: GraphNode[]; links: GraphLink[]; targets: Set<string>; selected: string | undefined; dead: Set<string>; onSelect: (node: GraphNode) => void };
const id = (value: string | GraphNode) => typeof value === 'string' ? value : value.id;
const amount = (value: number) => `${(value / 1e6).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} млн ₸`;

export default function FocusNetwork({ nodes, links, targets, selected, dead, onSelect }: Props) {
  const roots = nodes.filter(n => targets.has(n.id)).sort((a, b) => b.priority - a.priority);
  const seeds = nodes.filter(n => n.is_seed && !targets.has(n.id));
  const middle = nodes.filter(n => !targets.has(n.id) && !n.is_seed).sort((a, b) => (a.fy ?? 0) - (b.fy ?? 0));
  const positions = new Map<string, { x: number; y: number }>();
  seeds.forEach((n, i) => positions.set(n.id, { x: 65 + (i + .5) * 670 / Math.max(seeds.length, 1), y: 110 }));
  const rows = Math.max(1, Math.ceil(middle.length / 5));
  middle.forEach((n, i) => { const row = Math.floor(i / 5), count = Math.min(5, middle.length - row * 5); positions.set(n.id, { x: 90 + (i % 5 + .5) * 620 / count, y: 245 + row * 110 / rows }); });
  roots.forEach((n, i) => positions.set(n.id, { x: [400, 170, 630][i] ?? 400, y: i === 0 ? 475 : 455 }));
  const path = (edge: GraphLink) => { const a = positions.get(id(edge.source))!, b = positions.get(id(edge.target))!; const bend = Math.max(45, Math.abs(b.y - a.y) * .48); return `M ${a.x} ${a.y} C ${a.x} ${a.y + bend}, ${b.x} ${b.y - bend}, ${b.x} ${b.y}`; };
  return <div className="focus-art"><svg viewBox="0 0 800 610" role="img" aria-label="Избранные реальные пути от известных курьеров к трём приоритетным узлам">
    <defs>
      <radialGradient id="network-halo"><stop stopColor="#93d845" stopOpacity=".22"/><stop offset="1" stopColor="#93d845" stopOpacity="0"/></radialGradient>
      <linearGradient id="seed-cap" x2="0" y2="1"><stop stopColor="#e1e6ed"/><stop offset="1" stopColor="#737e8c"/></linearGradient>
      <filter id="core-bloom" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="10"/></filter>
      <marker id="flow-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse"><path d="M 0 1 L 9 5 L 0 9" fill="none" stroke="#b8dda8" strokeWidth="1.4"/></marker>
    </defs>
    <ellipse cx="400" cy="440" rx="330" ry="190" fill="url(#network-halo)"/>
    <path d="M 35 102 H 765" stroke="#ffffff" strokeOpacity=".07" strokeWidth="3"/>
    <text x="40" y="65" className="stratum-label">01 / ИЗВЕСТНЫЕ КУРЬЕРЫ</text>
    <text x="40" y="207" className="stratum-label">02 / ПУТИ ПЕРЕВОДОВ</text>
    <text x="40" y="392" className="stratum-label">03 / ПРИОРИТЕТ ПРОВЕРКИ</text>
    {links.filter(e => positions.has(id(e.source)) && positions.has(id(e.target))).map(edge => {
      const key = `${id(edge.source)}-${id(edge.target)}`, dim = dead.has(id(edge.source)) || dead.has(id(edge.target));
      const active = selected === id(edge.source) || selected === id(edge.target);
      return <g key={key} opacity={dim ? .12 : active ? 1 : .6}><path d={path(edge)} fill="none" stroke={targets.has(id(edge.target)) ? '#b0de62' : '#aed0b7'} strokeWidth={Math.min(3, .8 + Math.log10(Math.max(1, edge.sum_kzt)) / 4)} markerEnd="url(#flow-arrow)"/>{!dim && <circle r="2" fill="#efffd0"><animateMotion dur="7s" repeatCount="indefinite" path={path(edge)}/></circle>}</g>;
    })}
    {nodes.map(node => { const p = positions.get(node.id); if (!p) return null; const root = targets.has(node.id), primary = roots[0]?.id === node.id; const r = root ? primary ? 47 : 31 : 12, dim = dead.has(node.id); return <g key={node.id} transform={`translate(${p.x},${p.y})`} className="network-node" role="button" tabIndex={0} aria-label={`Открыть GID ${node.id}`} onClick={() => onSelect(node)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(node); } }}>
      <title>{`GID ${node.id} · колено ${node.depth} · приоритет ${(node.priority * 100).toFixed(1)}%`}</title>
      <circle r={root ? r + 12 : 22} fill="transparent"/>
      {node.is_seed && !root ? <g opacity={dim ? .25 : 1}><path d="M -2 0 L -2 15 L 2 15 L 2 0" fill="#909ba9"/><path d="M -11 0 A 11 12 0 0 1 11 0 Z" fill="url(#seed-cap)"/><text y="-22" className="seed-id">·{node.id.slice(-6)}</text></g> : <>
        {root && <circle r={r + 8} fill={dim ? '#27352a' : '#a3e635'} opacity=".55" filter="url(#core-bloom)"/>}
        <circle r={r} fill={dim ? '#303a33' : root ? '#ace34f' : '#17372b'} stroke={dim ? '#516054' : '#93d988'} strokeWidth="1.4"/>
        <circle r={r * .61} fill={dim ? '#4f5b51' : root ? '#dcff98' : '#87dba0'}/>
        <circle r={r + 5} fill="none" stroke={selected === node.id ? '#fff' : '#a3e635'} strokeOpacity={selected === node.id ? 1 : .4} strokeDasharray={root ? '2 4' : undefined}/>
        <text y={r + 22} className={root ? 'root-id' : 'seed-id'}>GID ·{node.id.slice(-6)}</text>
        {root && <text y={r + 39} className="root-value">{amount(node.in_kzt)} · входящий поток</text>}
      </>}
    </g>; })}
    <text x="400" y="595" textAnchor="middle" className="stratum-label">ВЫБРАННЫЕ ПУТИ · ВЕРТИКАЛЬ ПОКАЗЫВАЕТ ЭТАП МАРШРУТА, НЕ DEPTH</text>
  </svg></div>;
}
