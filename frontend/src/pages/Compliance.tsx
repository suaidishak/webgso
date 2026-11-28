import { useEffect, useState } from "react";
import { api, CATS, CAT_LABEL, fmt } from "../api";
import Icon, { Skeleton, Spinner } from "../Icon";
import { Pager, PlantPicker, regionsOf, usePlantIndex, useQueryState } from "../components";

type SummaryRow = { mnemonic: string; category: string; rule: string; severity: string; n: number; example: string; last_seen: string };
type Summary = { rows: SummaryRow[]; total: number; plants_affected: number; by_plant: { mnemonic: string; major: number; minor: number }[] };
type Deadline = { plant: string; category: string; due_at: string; target_start: string; first_valid_at: string | null; status: string };
type Deadlines = { rows: Deadline[]; total: number; counts: Record<string, number> };

const DEFAULTS = { plants: "", region: "", api: "", severity: "", hours: "24", sort: "count", dstatus: "", spage: "0", dpage: "0" };
const HOURS: [string, string][] = [["1", "Last hour"], ["6", "Last 6 hours"], ["24", "Last 24 hours"], ["168", "Last 7 days"], ["720", "Last 30 days"]];
const SIZE = 50;

export default function Compliance({ query = "" }: { query?: string }) {
  const [f, setQ] = useQueryState("/compliance", query, DEFAULTS);
  const set = (patch: Partial<typeof DEFAULTS>) => setQ({ ...patch, spage: "0", dpage: "0" });
  const [summary, setSummary] = useState<Summary | null>(null);
  const [deadlines, setDeadlines] = useState<Deadlines | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const index = usePlantIndex();
  const regions = regionsOf(index || []);
  const plants = f.plants ? f.plants.split(",") : [];

  const scope = () => {
    const p = new URLSearchParams();
    if (f.plants) p.set("plants", f.plants);
    if (f.region) p.set("region", f.region);
    if (f.api) p.set("category", f.api);
    return p;
  };
  useEffect(() => {
    let live = true;
    const p = scope();
    p.set("hours", f.hours); p.set("sort", f.sort); p.set("limit", String(SIZE)); p.set("offset", String(Number(f.spage) * SIZE));
    if (f.severity) p.set("severity", f.severity);
    setLoading(true);
    api<Summary>(`/compliance/summary?${p}`).then(d => live && setSummary(d)).catch(e => live && setErr(e.message)).finally(() => live && setLoading(false));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [f.plants, f.region, f.api, f.severity, f.hours, f.sort, f.spage]);
  useEffect(() => {
    let live = true;
    const p = scope();
    p.set("limit", String(SIZE)); p.set("offset", String(Number(f.dpage) * SIZE));
    if (f.dstatus) p.set("status", f.dstatus);
    api<Deadlines>(`/compliance/deadlines?${p}`).then(d => live && setDeadlines(d)).catch(e => live && setErr(e.message));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [f.plants, f.region, f.api, f.dstatus, f.dpage]);

  const counts = deadlines?.counts || {};
  const filtered = !!(f.plants || f.region || f.api || f.severity);
  const hoursLabel = (HOURS.find(h => h[0] === f.hours)?.[1] || `Last ${f.hours} hours`).toLowerCase();
  return (
    <>
      <div className="title-row"><h2>Compliance</h2>{loading && summary && <span className="muted small"><Spinner /> Loading…</span>}</div>
      <div className="card board-tools">
        <div className="board-row">
          <div className="field inline-field"><span>Plants</span><PlantPicker value={plants} onChange={v => set({ plants: v.join(",") })} allLabel="All plants" /></div>
          {regions.length > 1 && <label className="field inline-field"><span>Region</span>
            <select value={f.region} onChange={e => set({ region: e.target.value })}>
              <option value="">All regions</option>{regions.map(r => <option key={r.region} value={r.region}>{r.region}</option>)}
            </select></label>}
          <label className="field inline-field"><span>API</span>
            <select value={f.api} onChange={e => set({ api: e.target.value })}>
              <option value="">Any API</option>{CATS.map(c => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}
            </select></label>
          {filtered && <button className="ghost small-btn" onClick={() => setQ({ ...DEFAULTS, hours: f.hours, sort: f.sort })}><Icon name="x" size={12} />Clear filters</button>}
        </div>
      </div>
      {err && <div className="error" role="alert">{err}</div>}

      <div className="title-row section-row">
        <h3>Rule breaks, {hoursLabel}</h3>
        {summary && <span className="muted small">{summary.plants_affected} plant{summary.plants_affected === 1 ? "" : "s"} affected</span>}
        <span className="actions">
          <label className="small muted">Period <select value={f.hours} onChange={e => set({ hours: e.target.value })}>{HOURS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label className="small muted">Severity <select value={f.severity} onChange={e => set({ severity: e.target.value })}>
            <option value="">Both</option><option value="major">Major</option><option value="minor">Minor</option></select></label>
          <label className="small muted">Sort <select value={f.sort} onChange={e => set({ sort: e.target.value })}>
            <option value="count">Major and most frequent first</option><option value="plant">Plant</option></select></label>
        </span>
      </div>
      {summary && summary.by_plant.length > 1 && !f.plants && (
        <div className="chips top-plants" aria-label="Plants with the most rule breaks">
          <span className="small muted">Most breaks:</span>
          {summary.by_plant.map(b => <button key={b.mnemonic} className="chip" onClick={() => set({ plants: b.mnemonic })}
            title={`${b.major} major, ${b.minor} minor`}>{b.mnemonic.toUpperCase()}<span className="chip-n">{b.major ? `${b.major} major` : `${b.minor} minor`}</span></button>)}
        </div>
      )}
      {!summary ? <Skeleton rows={3} /> : !summary.rows.length ? <div className="card okline">No flags {hoursLabel}{filtered ? " for these filters" : ""}.</div> : (
        <>
          <div className="table-wrap"><table>
            <thead><tr><th>Plant</th><th>Endpoint</th><th>Severity</th><th>Rule</th><th className="num">Times</th><th>Last seen</th><th>Example</th></tr></thead>
            <tbody>{summary.rows.map((r, i) => (
              <tr key={i}><td><a href={`#/plant/${r.mnemonic}`} className="mnem">{r.mnemonic.toUpperCase()}</a></td><td>{CAT_LABEL[r.category]}</td>
                <td><span className={`sev ${r.severity}`}>{r.severity}</span></td><td><code>{r.rule}</code></td>
                <td className="num">{r.n}</td><td className="nowrap small">{fmt(r.last_seen)}</td><td className="small">{r.example}</td></tr>
            ))}</tbody>
          </table></div>
          <Pager total={summary.total} offset={Number(f.spage) * SIZE} limit={SIZE} noun="rows" onOffset={o => setQ({ spage: String(o / SIZE) })} />
        </>
      )}

      <div className="title-row section-row">
        <h3>Publish deadlines</h3>
        {deadlines && <div className="chips" role="group" aria-label="Deadline status">
          {([["", "All", (counts.met || 0) + (counts.late || 0) + (counts.missed || 0), ""], ["met", "Met", counts.met || 0, "green"], ["late", "Late", counts.late || 0, "amber"], ["missed", "Missed", counts.missed || 0, "red"]] as [string, string, number, string][]).map(([k, l, n]) => (
            <label key={k || "all"} className={`chip ${f.dstatus === k ? "on" : ""}`}><input type="radio" name="dstatus" checked={f.dstatus === k} onChange={() => setQ({ dstatus: k, dpage: "0" })} />{l}<span className="chip-n">{n}</span></label>
          ))}
        </div>}
      </div>
      <p className="muted small">Checked after each deadline plus its grace period: met, late (arrived after) or missed.</p>
      {!deadlines ? <Skeleton rows={4} /> : !deadlines.rows.length ? <p className="muted">{f.dstatus || filtered ? "No deadline checks match." : "No deadlines have passed since retrieval started."}</p> : (
        <>
          <div className="table-wrap"><table>
            <thead><tr><th>Due</th><th>Plant</th><th>Endpoint</th><th>Covers from</th><th>First valid</th><th>Status</th></tr></thead>
            <tbody>{deadlines.rows.map((d, i) => (
              <tr key={i}><td>{fmt(d.due_at)}</td><td><a href={`#/plant/${d.plant}`} className="mnem">{d.plant.toUpperCase()}</a></td><td>{CAT_LABEL[d.category]}</td>
                <td>{fmt(d.target_start).slice(0, 10)}</td><td>{fmt(d.first_valid_at)}</td>
                <td><span className={`badge ${d.status === "met" ? "green" : d.status === "late" ? "amber" : "red"}`}>{d.status}</span></td></tr>
            ))}</tbody>
          </table></div>
          <Pager total={deadlines.total} offset={Number(f.dpage) * SIZE} limit={SIZE} noun="deadline checks" onOffset={o => setQ({ dpage: String(o / SIZE) })} />
        </>
      )}
    </>
  );
}
