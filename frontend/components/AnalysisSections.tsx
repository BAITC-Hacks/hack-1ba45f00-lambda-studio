'use client';
import { useEffect, useState, ReactNode } from 'react';
import { api, Answer, Brief, Health, Resilience, Sankey, TopBlock, DataMode } from '../lib/api';

const money = (n: number) => `${(n / 1e6).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} млн ₸`;
const percent = (n: number) => `${n.toLocaleString('ru-RU', { maximumFractionDigits: 1 })}%`;
const errorText = (e: unknown) => e instanceof Error ? e.message : 'Не удалось получить данные.';
function useLoad<T>(load: () => Promise<T>) {
  const [value, setValue] = useState<T | null>(null), [error, setError] = useState(''), [revision, setRevision] = useState(0);
  useEffect(() => { let active = true; setError(''); load().then(v => { if (active) setValue(v); }).catch(e => { if (active) setError(errorText(e)); }); return () => { active = false; }; }, [load, revision]);
  return { value, error, retry: () => setRevision(v => v + 1) };
}
function Loading({ error, retry }: { error: string; retry: () => void }) { return <div className="section-state" role="status">{error || 'Загружаем расчёты…'}{error && <button onClick={retry}>Повторить</button>}</div>; }

export function MoneyLadder() {
  const { value, error, retry } = useLoad(api.sankey);
  if (!value) return <Loading error={error} retry={retry}/>;
  return <section className="analysis-section"><div className="section-heading"><div><span className="eyebrow">ДВИЖЕНИЕ СРЕДСТВ</span><h2>Лестница денег</h2><p>Колонки — колена сети. Толщина каждой ленты пропорциональна сумме переводов.</p></div><span className="demo-tag">{value.nodes.length} групп</span></div><SankeyDrawing data={value}/><div className="analysis-note">{money(value.skipped_kzt)} ({percent(value.skipped_kzt / (value.links.reduce((s, l) => s + l.value, 0) + value.skipped_kzt) * 100)}) переводов идут вбок или назад и не показаны на лестнице. Их можно исследовать в режиме «Ядро» на графе.</div><details><summary>Точные суммы переводов между группами</summary><table><thead><tr><th>Откуда</th><th>Куда</th><th>Сумма</th></tr></thead><tbody>{value.links.map((link, i) => <tr key={i}><td>{link.source}</td><td>{link.target}</td><td>{money(link.value)}</td></tr>)}</tbody></table></details></section>;
}

function SankeyDrawing({ data }: { data: Sankey }) {
  const padding = 12, usable = 440;
  const groups = Array.from({ length: 5 }, (_, depth) => data.nodes.filter(n => Number(n.name.split(' ')[0]) === depth));
  const volume = (name: string) => Math.max(data.links.filter(l => l.source === name).reduce((s,l) => s+l.value,0), data.links.filter(l => l.target === name).reduce((s,l) => s+l.value,0));
  const scale = Math.min(...groups.filter(g => g.length).map(g => (usable-padding*(g.length-1))/Math.max(1,g.reduce((s,n)=>s+volume(n.name),0))));
  const positions = new Map<string,{x:number;y:number;h:number}>();
  groups.forEach((group, depth) => { let y=65; group.forEach(n=>{ const h=volume(n.name)*scale; positions.set(n.name,{x:50+depth*222,y,h}); y+=h+padding; }); });
  const outgoing = new Map<string,number>(), incoming = new Map<string,number>();
  const ribbons = data.links.map((l,i)=>{ const a=positions.get(l.source), b=positions.get(l.target); if(!a||!b)return null; const h=l.value*scale, y1=a.y+(outgoing.get(l.source)||0), y2=b.y+(incoming.get(l.target)||0); outgoing.set(l.source,(outgoing.get(l.source)||0)+h); incoming.set(l.target,(incoming.get(l.target)||0)+h); const x1=a.x+12,x2=b.x,mid=(x1+x2)/2; return <path key={i} d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2} L${x2},${y2+h} C${mid},${y2+h} ${mid},${y1+h} ${x1},${y1+h} Z`} fill={`hsl(${95+Number(l.source[0])*25} 42% 58%)`} opacity=".35"><title>{l.source} → {l.target}: {money(l.value)}</title></path>; });
  return <svg className="sankey" viewBox="0 0 1030 550" role="img" aria-label="Переводы по коленам сети">{groups.map((_,i)=><text key={i} x={50+i*222} y="30" fill="#a3e635" fontSize="12">{i===0?'КУРЬЕРЫ':`КОЛЕНО ${i}`}</text>)}{ribbons}{data.nodes.map(n=>{const p=positions.get(n.name); if(!p)return null; return <g key={n.name}><rect x={p.x} y={p.y} height={Math.max(.5,p.h)} width="12" rx="2" fill="#b7dd85"/><text x={p.x+17} y={p.y+Math.max(9,p.h/2)} fill="#dee7d9" fontSize="9">{n.name.replace(/^\d+ · /,'')}</text><title>{n.name}: {money(volume(n.name))}</title></g>;})}</svg>;
}

const strategyNames: Record<string,string> = {plan:'План блокировки',priority:'Топ проверки',turnover:'Топ по обороту',random:'Случайные узлы (среднее)'};
export function BlockingSection({ mode, focus, apply, busy }: { mode: DataMode; focus:(id:string)=>void; apply:(ids:string[])=>Promise<void>; busy:boolean }) {
  const {value,error,retry}=useLoad(api.resilience); const plan=useLoad(api.topBlock); const [count,setCount]=useState('10'); const [notice,setNotice]=useState('');
  if(!value||!plan.value)return <Loading error={error||plan.error} retry={()=>{retry();plan.retry();}}/>;
  return <section className="analysis-section"><div className="section-heading"><div><span className="eyebrow">МОДЕЛИРОВАНИЕ</span><h2>Проверять и блокировать — разные задачи</h2><p>Рейтинг проверки объясняет роли. План блокировки оценивает снижение потока денег курьеров.</p></div><select aria-label="Количество узлов в стратегии" value={count} onChange={e=>setCount(e.target.value)}>{['5','10','20'].map(n=><option key={n} value={n}>{n} узлов</option>)}</select></div><div className="strategy-grid">{Object.entries(value.strategies).map(([key,counts])=>{const s=counts[count]; if(!s)return null; return <div className="strategy" key={key}><h3>{strategyNames[key]||key}</h3><strong>−{percent(s.drop_reach_pct)}</strong><p>поток денег курьеров</p><div className="meter"><i style={{width:`${Math.max(0,Math.min(100,s.drop_reach_pct))}%`}}/></div><p>Глубокий поток: −{percent(s.drop_deep_pct)}</p><small>Фрагментов сети: {s.n_components.toLocaleString('ru-RU')}</small>{s.removed&&<button disabled={mode!=='api'||busy} onClick={async()=>{setNotice('');try{await apply(s.removed!);setNotice('Расчёт выполнен. Результат и затухание путей показаны на графе.');}catch(e){setNotice(errorText(e));}}}>{busy?'Рассчитываем…':'Смоделировать на графе'}</button>}</div>;})}</div>{mode!=='api'&&<div className="analysis-note">Показаны сохранённые расчёты. Для нового моделирования подключите сервер.</div>}{notice&&<p role="status">{notice}</p>}<h3>План блокировки</h3><table><thead><tr><th>Шаг</th><th>GID</th><th>Дополнительно отсекается</th><th>Обоснование</th></tr></thead><tbody>{plan.value.map(row=><tr key={row.id}><td>{row.step}</td><td><button className="text-link" onClick={()=>focus(row.id)}>{row.id}</button></td><td>{money(row.cut_kzt_marginal)}</td><td>{row.why}</td></tr>)}</tbody></table></section>;
}

function inline(text:string, focus:(id:string)=>void):ReactNode[] {
  return text.split(/(\[gid:\d+\]|\*\*[^*]+\*\*)/g).map((part,i)=>part.startsWith('[gid:')?<button key={i} className="text-link" onClick={()=>focus(part.slice(5,-1))}>{part.slice(5,-1)}</button>:part.startsWith('**')?<strong key={i}>{part.slice(2,-2)}</strong>:part);
}
function Markdown({text,focus}:{text:string;focus:(id:string)=>void}) { return <div className="brief-text">{text.split('\n').map((line,i)=>line.startsWith('#')?<h3 key={i}>{inline(line.replace(/^#+\s*/,''),focus)}</h3>:line.startsWith('- ')?<p className="brief-bullet" key={i}>• {inline(line.slice(2),focus)}</p>:<p key={i}>{inline(line,focus)}</p>)}</div>; }
function Verification({verified,issues}:{verified:boolean;issues:string[]}){return <div className={verified?'verified':'unverified'}>{verified?'✓ Сверено с графом':'⚠ Не сверено'}{!verified&&issues.map((issue,i)=><p key={i}>{issue}</p>)}</div>;}
export function AISection({mode,health,focus}:{mode:DataMode;health:Health|null;focus:(id:string)=>void}) {
  const {value,error,retry}=useLoad(api.brief); const [question,setQuestion]=useState(''),[answer,setAnswer]=useState<Answer|null>(null),[busy,setBusy]=useState(false),[failure,setFailure]=useState('');
  const enabled=mode==='api'&&health?.ai_enabled;
  return <section className="analysis-section ai-section"><div className="section-heading"><div><span className="eyebrow">АНАЛИТИЧЕСКАЯ СПРАВКА</span><h2>Справка ИИ</h2><p>Все выводы — гипотезы для проверки. Нажмите GID в тексте, чтобы открыть узел.</p></div><span className="demo-tag">Сохранённая версия</span></div>{!value?<Loading error={error} retry={retry}/>:<><Verification verified={value.verified} issues={value.issues}/><Markdown text={value.markdown} focus={focus}/></>}{enabled?<form className="ask-form" onSubmit={async e=>{e.preventDefault();if(!question.trim()||busy)return;setBusy(true);setFailure('');setAnswer(null);try{setAnswer(await api.ask(question.trim()));}catch(e){setFailure(errorText(e));}finally{setBusy(false);}}}><label htmlFor="analyst-question">Задать вопрос по графу</label><textarea id="analyst-question" onKeyDown={e=>{ if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} maxLength={1000} value={question} onChange={e=>setQuestion(e.target.value)} placeholder="Почему этот узел рекомендуют проверить?"/><button disabled={busy||!question.trim()}>{busy?'Аналитик проверяет граф…':'Задать вопрос'}</button></form>:<div className="analysis-note">Живые вопросы сейчас недоступны. Сохранённая справка доступна полностью.</div>}{failure&&<p role="alert" className="unverified">{failure}</p>}{answer&&<div className="answer"><Verification verified={answer.verified} issues={answer.issues}/><Markdown text={answer.answer} focus={focus}/><details><summary>Какие запросы к графу сделал ИИ</summary><pre>{JSON.stringify(answer.tool_calls,null,2)}</pre></details></div>}</section>;
}
