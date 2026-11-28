import { useEffect, useState } from "react";
import { api, CATS, CAT_LABEL, fmt } from "../api";
import Icon, { Skeleton, Spinner } from "../Icon";
import { BulkBar, PageCheck, Pager, PlantPicker, regionsOf, useDebounced, usePlantIndex, useQueryState } from "../components";

type Alert = { id: number; plant: string; category: string; rule: string; severity: string; message: string; raised_at: string;
  last_seen_at: string; occurrences: number; cleared_at: string | null; acknowledged_at: string | null };
type Page = { rows: Alert[]; total: number; rules: { rule: string; n: number }[]; by_plant: { plant: string; n: number }[] };

const DEFAULTS = { open: "1", plants: "", region: "", api: "", severity: "", rule: "", acked: "", q: "", sort: "newest", size: "50", page: "0" };
const SORTS: [string, string][] = [["newest", "Newest first"], ["oldest", "Oldest first"], ["last_seen", "Last seen"], ["occurrences", "Most repeated"], ["plant", "Plant"]];

export default function Alerts({ canAck, query = "" }: { canAck: boolean; query?: string }) {
  const [f, setQ] = useQueryState("/alerts", query, DEFAULTS);
  const set = (patch: Partial<typeof DEFAULTS>) => { setQ({ ...patch, page: "0" }); setSel(new Set()); };
  const [data, setData] = useState<Page | null>(null);
  const [loading, setLoading] = useState(false);
  const [acking, setAcking] = useState<number | "bulk" | null>(null);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [tick, setTick] = useState(0);
  const index = usePlantIndex();
  const regions = regionsOf(index || []);
  const q = useDebounced(f.q);
  const size = Number(f.size) || 50;
  const offset = Number(f.page) * size;
  const plants = f.plants ? f.plants.split(",") : [];

  useEffect(() => {
    let live = true;
    const p = new URLSearchParams({ open: f.open === "1" ? "true" : "false", sort: f.sort, limit: String(size), offset: String(offset) });
    if (f.plants) p.set("plants", f.plants);
    if (f.region) p.set("region", f.region);
    if (f.api) p.set("category", f.api);
    if (f.severity) p.set("severity", f.severity);
    if (f.rule) p.set("rule", f.rule);
    if (f.acked) p.set("acked", f.acked);
    if (q.trim()) p.set("q", q.trim());
    setLoading(true);
    api<Page>(`/alerts?${p}`).then(d => { if (live) { setData(d); setErr(""); } }).catch(e => live && setErr(e.message))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [f.open, f.plants, f.region, f.api, f.severity, f.rule, f.acked, q, f.sort, size, offset, tick]);

  const ack = async (ids: number[], who: number | "bulk") => {
    setAcking(who); setErr("");
    try {
      if (ids.length === 1 && who !== "bulk") await api(`/alerts/${ids[0]}/ack`, { method: "POST" });
      else await api("/alerts/ack", { method: "POST", body: { ids } });
      setSel(new Set()); setTick(t => t + 1);
    } catch (e: any) { setErr(`Couldn't acknowledge: ${e.message}`); }
    setAcking(null);
  };
  const filtered = !!(f.plants || f.region || f.api || f.severity || f.rule || f.acked || f.q);
  const ackable = (data?.rows || []).filter(a => !a.cleared_at && !a.acknowledged_at).map(a => String(a.id));

  return (
    <>
      <div className="title-row">
        <h2>Alert log</h2>
        {data && <span className="badge">{data.total.toLocaleString()} {f.open === "1" ? "open" : "total"}</span>}
        {loading && data && <span className="muted small"><Spinner /> Loading…</span>}
        <label className="toggle push-right"><input type="checkbox" checked={f.open === "1"} onChange={e => set({ open: e.target.checked ? "1" : "0" })} /> Open only</label>
      </div>
      <p className="muted small">Alerts are raised automatically from major compliance flags, failed calls and missed deadlines, and cleared when a later submission is clean. They are kept as an audit log and never deleted.</p>

      <div className="card board-tools">
        <div className="board-row">
          <input className="search" type="search" placeholder="Search message, rule or plant" aria-label="Search alerts" value={f.q} onChange={e => set({ q: e.target.value })} />
          <div className="field inline-field"><span>Plants</span><PlantPicker value={plants} onChange={v => set({ plants: v.join(",") })} allLabel="All plants" /></div>
          {regions.length > 1 && <label className="field inline-field"><span>Region</span>
            <select value={f.region} onChange={e => set({ region: e.target.value })}>
              <option value="">All regions</option>{regions.map(r => <option key={r.region} value={r.region}>{r.region}</option>)}
            </select></label>}
          <label className="field inline-field"><span>API</span>
            <select value={f.api} onChange={e => set({ api: e.target.value })}>
              <option value="">Any API</option>{CATS.map(c => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}
            </select></label>
          <label className="field inline-field"><span>Rule</span>
            <select value={f.rule} onChange={e => set({ rule: e.target.value })}>
              <option value="">Any rule</option>
              {(data?.rules || []).map(r => <option key={r.rule} value={r.rule}>{r.rule} ({r.n})</option>)}
              {f.rule && !(data?.rules || []).some(r => r.rule === f.rule) && <option value={f.rule}>{f.rule}</option>}
            </select></label>
          {canAck && <label className="field inline-field"><span>Acknowledged</span>
            <select value={f.acked} onChange={e => set({ acked: e.target.value })}>
              <option value="">Either</option><option value="no">Not yet</option><option value="yes">Yes</option>
            </select></label>}
          <label className="field inline-field"><span>Sort</span>
            <select value={f.sort} onChange={e => set({ sort: e.target.value })}>{SORTS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          {filtered && <button className="ghost small-btn" onClick={() => setQ({ ...DEFAULTS, open: f.open, sort: f.sort, size: f.size })}><Icon name="x" size={12} />Clear filters</button>}
        </div>
        {data && data.by_plant.length > 1 && !f.plants && (
          <div className="chips top-plants" aria-label="Plants with the most matching alerts">
            <span className="small muted">Most alerts:</span>
            {data.by_plant.map(b => <button key={b.plant} className="chip" onClick={() => set({ plants: b.plant })}>{b.plant.toUpperCase()}<span className="chip-n">{b.n}</span></button>)}
          </div>
        )}
      </div>

      {canAck && (
        <BulkBar count={sel.size} onClear={() => setSel(new Set())} noun="alert">
          <button className="small-btn" disabled={acking !== null} onClick={() => ack([...sel].map(Number), "bulk")}>{acking === "bulk" ? <><Spinner />Saving…</> : "Acknowledge selected"}</button>
        </BulkBar>
      )}
      {err && <div className="error" role="alert">{err}</div>}
      {!data ? (err ? null : <Skeleton rows={5} />) : !data.rows.length ? (
        <div className="card okline">{filtered ? "No alerts match these filters." : `No ${f.open === "1" ? "open " : ""}alerts. Every retrieved endpoint is clean.`}</div>
      ) : (
        <div className="table-wrap"><table>
          <thead><tr>
            {canAck && <th className="sel-col"><PageCheck ids={ackable} sel={sel} onChange={setSel} label="Select every open, unacknowledged alert on this page" /></th>}
            <th>Raised</th><th>Plant</th><th>Endpoint</th><th>Rule</th><th>Message</th><th className="num">Seen</th><th>Last seen</th><th>Cleared</th><th></th></tr></thead>
          <tbody>{data.rows.map(a => (
            <tr key={a.id} className={sel.has(String(a.id)) ? "sel" : ""}>
              {canAck && <td className="sel-col">{!a.cleared_at && !a.acknowledged_at &&
                <input type="checkbox" aria-label={`Select alert ${a.rule} on ${a.plant.toUpperCase()}`} checked={sel.has(String(a.id))}
                  onChange={e => setSel(s => { const n = new Set(s); e.target.checked ? n.add(String(a.id)) : n.delete(String(a.id)); return n; })} />}</td>}
              <td className="nowrap">{fmt(a.raised_at)}</td><td><a href={`#/plant/${a.plant}`} className="mnem">{a.plant.toUpperCase()}</a></td>
              <td>{CAT_LABEL[a.category] || a.category}</td><td><code>{a.rule}</code></td><td className="msg-cell">{a.message}</td>
              <td className="num">{a.occurrences}×</td><td>{fmt(a.last_seen_at)}</td>
              <td>{a.cleared_at ? fmt(a.cleared_at) : <span className="badge red">open</span>}</td>
              <td>{a.acknowledged_at ? <span className="muted small">ack {fmt(a.acknowledged_at).slice(5)}</span>
                : canAck && !a.cleared_at ? <button className="ghost small-btn" disabled={acking !== null} onClick={() => ack([a.id], a.id)}>{acking === a.id ? <><Spinner />Saving…</> : "Acknowledge"}</button> : null}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {data && <Pager total={data.total} offset={offset} limit={size} noun="alerts" sizes={[25, 50, 100, 250, 500]}
        onOffset={o => setQ({ page: String(Math.floor(o / size)) })} onLimit={n => setQ({ size: String(n), page: "0" })} />}
    </>
  );
}

