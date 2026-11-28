import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { api, CATS, CAT_LABEL, fmt } from "../api";
import Icon, { Skeleton, Spinner } from "../Icon";
import { BulkBar, NO_REGION, PageCheck, Pager, invalidatePlantIndex, regionsOf, usePlantIndex, useQueryState } from "../components";

type Endpoint = { category: string; url: string; enabled: boolean; note: string | null; polled?: boolean; off_reason?: string | null };
type PlantRow = {
  mnemonic: string; name: string; region: string | null; capacity_mw: number | null; mmf_station_count: number | null; status: string;
  notes: string | null; endpoints: Endpoint[]; auth_method: string; auth_param_name: string | null;
  has_api_key: boolean; api_key_hint: string | null; updated_at: string | null; polling_enabled: boolean;
};
type Polling = { fleet: boolean; types: Record<string, boolean> };

export function Switch({ on, onChange, label, disabled, title }: { on: boolean; onChange: (v: boolean) => void; label?: string; disabled?: boolean; title?: string }) {
  return (
    <label className={`switch ${on ? "on" : ""} ${disabled ? "dis" : ""}`} title={title}>
      <input type="checkbox" role="switch" aria-label={label ? undefined : title} checked={on} disabled={disabled} onChange={e => onChange(e.target.checked)} />
      <span className="track"><span className="knob" /></span>
      {label && <span className="switch-label">{label}</span>}
    </label>
  );
}

function PollingPanel({ polling, onChange }: { polling: Polling; onChange: (p: Polling) => void }) {
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState("");
  const setFleet = async (enabled: boolean) => {
    setErr(""); setConfirm(false);
    try { onChange(await api("/admin/polling/fleet", { method: "PUT", body: { enabled } })); } catch (e: any) { setErr(e.message); }
  };
  const setType = async (c: string, enabled: boolean) => {
    setErr("");
    try { onChange(await api(`/admin/polling/types/${c}`, { method: "PUT", body: { enabled } })); } catch (e: any) { setErr(e.message); }
  };
  return (
    <div className={`card polling ${polling.fleet ? "" : "paused"}`}>
      <div className="polling-fleet">
        <div>
          <b>Fleet retrieval</b>
          <div className="small muted">{polling.fleet ? "Scheduled retrieval is running for every plant." : "All scheduled retrieval is paused. Tests and Retrieve now still work."}</div>
        </div>
        {confirm ? (
          <span className="actions">
            <span className="small">Pause retrieval for every plant?</span>
            <button className="danger-fill" onClick={() => setFleet(false)}>Pause all</button>
            <button className="ghost" onClick={() => setConfirm(false)}>Cancel</button>
          </span>
        ) : (
          <Switch on={polling.fleet} label={polling.fleet ? "On" : "Paused"} onChange={v => (v ? setFleet(true) : setConfirm(true))} />
        )}
      </div>
      <div className="polling-types">
        <span className="small muted">Endpoint types, for all plants:</span>
        {CATS.map(c => (
          <Switch key={c} on={polling.types[c] !== false} label={CAT_LABEL[c]} disabled={!polling.fleet}
            title={polling.fleet ? `Pause or resume ${CAT_LABEL[c]} for every plant` : "Fleet retrieval is paused"}
            onChange={v => setType(c, v)} />
        ))}
      </div>
      {err && <div className="error small">{err}</div>}
    </div>
  );
}

const DEFAULT_PATHS: Record<string, string> = {
  rolling: "forecast/rolling", dayahead: "forecast/dayahead", weekahead: "forecast/weekahead",
  fourmonthsahead: "forecast/fourmonthsahead", mmf: "mmf/live",
};
const AUTH_LABEL: Record<string, string> = { none: "None", header: "Header", query: "Query parameter" };

function KeyBadge({ p }: { p: PlantRow }) {
  if (p.auth_method === "none") return <span className="muted small">No key</span>;
  return (
    <span className="small">
      {p.auth_method === "header" ? `Header ${p.auth_param_name}` : `?${p.auth_param_name}=`}{" "}
      <code>{p.has_api_key ? `••••${p.api_key_hint || ""}` : "missing"}</code>
    </span>
  );
}

type TestResult = {
  category: string; url: string; polled: boolean; verdict: "pass" | "warn" | "fail"; reason: string;
  http_status: number | null; latency_ms: number | null; bytes: number | null; tested_at: string; attempt_id: number | null;
  major: string[]; minor: string[];
};
const VERDICT_LABEL = { pass: "Pass", warn: "Pass with warnings", fail: "Fail" };

export async function runTest(mnem: string, category?: string): Promise<TestResult[]> {
  const r = await api(`/admin/plants/${mnem}/test${category ? `?category=${category}` : ""}`, { method: "POST" });
  return r.results;
}

export function Verdict({ r }: { r: TestResult | "running" | undefined }) {
  if (r === undefined) return <span className="muted small">Not tested</span>;
  if (r === "running") return <span className="verdict running"><Spinner />Testing…</span>;
  const all = [...r.major.map(m => ["major", m]), ...r.minor.map(m => ["minor", m])];
  return (
    <span className="verdict-wrap">
      <span className={`verdict ${r.verdict}`}>{VERDICT_LABEL[r.verdict]}</span>
      <span className="small muted">{r.http_status ? `HTTP ${r.http_status}` : "no response"} · {r.latency_ms ?? "—"} ms
        {r.bytes ? ` · ${r.bytes >= 1024 ? Math.round(r.bytes / 1024) + " KB" : r.bytes + " B"}` : ""} · {fmt(r.tested_at).slice(11)}</span>
      <span className="small">{r.reason}{r.attempt_id && r.verdict !== "pass" ? <> · <a href={`#/attempt/${r.attempt_id}`}>see where</a></> : null}</span>
      {all.length > 1 && (
        <details className="small"><summary>{r.major.length} major, {r.minor.length} minor findings</summary>
          <ul className="flags">{all.map(([sev, m], i) => <li key={i}><span className={`sev ${sev}`}>{sev}</span>{m}</li>)}</ul>
        </details>
      )}
    </span>
  );
}

type TestState = Record<string, TestResult | "running">;

function useTests() {
  const [tests, setTests] = useState<TestState>({});
  const [err, setErr] = useState("");
  const test = async (mnem: string, cats: string[], category?: string) => {
    setErr("");
    setTests(t => ({ ...t, ...Object.fromEntries(cats.map(c => [`${mnem}:${c}`, "running" as const])) }));
    try {
      const res = await runTest(mnem, category);
      setTests(t => ({ ...t, ...Object.fromEntries(res.map(r => [`${mnem}:${r.category}`, r])) }));
    } catch (e: any) {
      setErr(`${mnem.toUpperCase()}: ${e.message}`);
      setTests(t => Object.fromEntries(Object.entries(t).filter(([k, v]) => !(k.startsWith(mnem + ":") && v === "running"))));
    }
  };
  return { tests, test, err };
}

function PlantSummary({ p, tests }: { p: PlantRow; tests: TestState }) {
  const rs = p.endpoints.map(e => tests[`${p.mnemonic}:${e.category}`]).filter(Boolean);
  if (!rs.length) return null;
  if (rs.includes("running")) return <span className="verdict running"><Spinner />Testing…</span>;
  const done = rs as TestResult[];
  const n = (v: string) => done.filter(r => r.verdict === v).length;
  return (
    <span className="small actions">
      {n("pass") > 0 && <span className="verdict pass">{n("pass")} pass</span>}{" "}
      {n("warn") > 0 && <span className="verdict warn">{n("warn")} warn</span>}{" "}
      {n("fail") > 0 && <span className="verdict fail">{n("fail")} fail</span>}
    </span>
  );
}

const PLANT_SORTS: [string, string][] = [["mnemonic", "Mnemonic A–Z"], ["name", "Name A–Z"], ["region", "Region"], ["capacity", "Largest capacity"], ["updated", "Recently changed"]];
const LIST_DEFAULTS = { q: "", region: "", status: "", polling: "", key: "", sort: "mnemonic", size: "50", page: "0" };

/** Run async jobs with at most n at once (testing 100+ plants one by one would take far too long). */
async function pool<T>(items: T[], n: number, fn: (x: T) => Promise<unknown>) {
  let i = 0;
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, async () => {
    while (i < items.length) { const x = items[i++]; await fn(x); }
  }));
}

export function PlantsList({ query = "" }: { query?: string }) {
  const [rows, setRows] = useState<PlantRow[] | null>(null);
  const [f, setQ] = useQueryState("/admin/plants", query, LIST_DEFAULTS);
  const set = (patch: Partial<typeof LIST_DEFAULTS>) => setQ({ ...patch, page: "0" });
  const [deleting, setDeleting] = useState<PlantRow | null>(null);
  const [importing, setImporting] = useState(false);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const { tests, test, err } = useTests();
  const [polling, setPolling] = useState<Polling | null>(null);
  const [toggleErr, setToggleErr] = useState("");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [bulkCat, setBulkCat] = useState("mmf");
  const [bulkStatus, setBulkStatus] = useState("live");
  const [bulkRegion, setBulkRegion] = useState("");
  const load = () => { api<PlantRow[]>("/plants").then(setRows); api<Polling>("/admin/polling").then(setPolling); };
  const replaceRow = (p: PlantRow) => setRows(rs => rs && rs.map(r => (r.mnemonic === p.mnemonic ? p : r)));
  const togglePlant = async (p: PlantRow, enabled: boolean) => {
    setToggleErr("");
    try { replaceRow(await api(`/admin/plants/${p.mnemonic}/polling`, { method: "PUT", body: { enabled } })); } catch (e: any) { setToggleErr(e.message); }
  };
  const toggleEndpoint = async (p: PlantRow, c: string, enabled: boolean) => {
    setToggleErr("");
    try { replaceRow(await api(`/admin/plants/${p.mnemonic}/endpoints/${c}/polling`, { method: "PUT", body: { enabled } })); } catch (e: any) { setToggleErr(e.message); }
  };
  const onPolling = (pl: Polling) => { setPolling(pl); api<PlantRow[]>("/plants").then(setRows); };
  const testPlant = (p: PlantRow, expand = true) => {
    if (expand) setOpen(o => ({ ...o, [p.mnemonic]: true }));
    return test(p.mnemonic, p.endpoints.map(e => e.category));
  };
  const [testingAll, setTestingAll] = useState(false);
  const testMany = async (ps: PlantRow[]) => {
    setTestingAll(true);
    try { await pool(ps.filter(p => p.endpoints.length), 4, p => testPlant(p, ps.length <= 10)); } finally { setTestingAll(false); }
  };
  useEffect(() => { load(); }, []);

  const regions = useMemo(() => regionsOf((rows || []).map(r => ({ mnemonic: r.mnemonic, name: r.name, region: r.region, status: r.status }))), [rows]);
  const shown = useMemo(() => {
    const n = f.q.trim().toLowerCase();
    const out = (rows || []).filter(p =>
      (!n || (p.mnemonic + " " + p.name + " " + (p.region || "")).toLowerCase().includes(n))
      && (!f.region || (p.region || NO_REGION) === f.region)
      && (!f.status || p.status === f.status)
      && (!f.polling || (f.polling === "on") === p.polling_enabled)
      && (!f.key || (f.key === "missing" ? p.auth_method !== "none" && !p.has_api_key : f.key === "set" ? p.has_api_key : p.auth_method === "none")));
    const by: Record<string, (a: PlantRow, b: PlantRow) => number> = {
      mnemonic: (a, b) => a.mnemonic.localeCompare(b.mnemonic),
      name: (a, b) => a.name.localeCompare(b.name),
      region: (a, b) => (a.region || "~").localeCompare(b.region || "~") || a.mnemonic.localeCompare(b.mnemonic),
      capacity: (a, b) => (b.capacity_mw || 0) - (a.capacity_mw || 0),
      updated: (a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""),
    };
    return out.sort(by[f.sort] || by.mnemonic);
  }, [rows, f.q, f.region, f.status, f.polling, f.key, f.sort]);
  if (!rows) return <Skeleton rows={8} />;
  const size = Number(f.size) || 0;
  const offset = size ? Number(f.page) * size : 0;
  const page = size ? shown.slice(offset, offset + size) : shown;
  const filtered = !!(f.q || f.region || f.status || f.polling || f.key);
  const selRows = rows.filter(r => sel.has(r.mnemonic));

  const bulk = async (label: string, body: Record<string, unknown>) => {
    setBusy(label); setNote(""); setToggleErr("");
    try {
      const r = await api("/admin/plants/bulk", { method: "POST", body: { plants: [...sel], ...body } });
      setNote(`${label}: ${r.changed} plant${r.changed === 1 ? "" : "s"} changed${r.unchanged ? `, ${r.unchanged} already set` : ""}${r.skipped.length ? `, ${r.skipped.length} without that API` : ""}.`);
      invalidatePlantIndex();
      load();
    } catch (e: any) { setToggleErr(e.message); }
    setBusy("");
  };
  const busyIcon = (label: string, icon: string) => busy === label ? <Spinner /> : <Icon name={icon} size={14} />;

  return (
    <>
      <div className="title-row">
        <h2>Plants</h2>
        <span className="muted small">{rows.length} registered{filtered ? ` · ${shown.length} match` : ""}</span>
        <span className="actions push-right">
          <button className="ghost" onClick={() => testMany(shown)} disabled={testingAll || !shown.length}
            title={filtered ? "Test every endpoint of the plants that match the filters" : "Test every endpoint of every plant"}>
            {testingAll ? <><Spinner />Testing…</> : filtered ? `Test ${shown.length} shown` : "Test all plants"}</button>
          <button className="ghost" onClick={() => setImporting(true)}><Icon name="table" />Import plants</button>
          <a className="button" href="#/admin/plants/new"><Icon name="plus" />Add plant</a>
        </span>
      </div>
      <p className="muted small">The scheduler reads this registry and the retrieval switches on every tick, so changes apply within 30 seconds. An endpoint is retrieved only when the fleet, its endpoint type, its plant and the endpoint itself are all switched on (and the plant is live). API keys are encrypted in the database and only their last 4 characters are shown. Test calls each endpoint now with the stored key: <b>Pass</b> means HTTP 200 and rev5-compliant, <b>Pass with warnings</b> means usable data with minor deviations, <b>Fail</b> means unreachable, an error status, or data that breaks a major rule. Tick plants to pause, resume, test or retrieve many at once.</p>
      {polling && <PollingPanel polling={polling} onChange={onPolling} />}

      <div className="card board-tools">
        <div className="board-row">
          <input className="search" type="search" placeholder="Search plant, name or region" aria-label="Search plants" value={f.q} onChange={e => set({ q: e.target.value })} />
          {regions.length > 1 && <label className="field inline-field"><span>Region</span>
            <select value={f.region} onChange={e => set({ region: e.target.value })}>
              <option value="">All regions</option>{regions.map(r => <option key={r.region} value={r.region}>{r.region} ({r.n})</option>)}
            </select></label>}
          <label className="field inline-field"><span>Status</span>
            <select value={f.status} onChange={e => set({ status: e.target.value })}>
              <option value="">Any</option><option value="live">Live</option><option value="onboarding">Onboarding</option><option value="suspended">Suspended</option>
            </select></label>
          <label className="field inline-field"><span>Retrieval</span>
            <select value={f.polling} onChange={e => set({ polling: e.target.value })}>
              <option value="">Any</option><option value="on">On</option><option value="paused">Paused</option>
            </select></label>
          <label className="field inline-field"><span>API key</span>
            <select value={f.key} onChange={e => set({ key: e.target.value })}>
              <option value="">Any</option><option value="set">Key set</option><option value="missing">Key missing</option><option value="none">No key used</option>
            </select></label>
          <label className="field inline-field"><span>Sort</span>
            <select value={f.sort} onChange={e => set({ sort: e.target.value })}>{PLANT_SORTS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          {filtered && <button className="ghost small-btn" onClick={() => setQ({ ...LIST_DEFAULTS, sort: f.sort, size: f.size })}><Icon name="x" size={12} />Clear filters</button>}
        </div>
      </div>

      <BulkBar count={sel.size} matched={shown.length} onSelectAll={() => setSel(new Set(shown.map(p => p.mnemonic)))} onClear={() => setSel(new Set())}>
        <a className="button small-btn" href={`#/admin/poll/${[...sel].join(",")}/all/run`}><Icon name="zap" size={14} />Retrieve now</a>
        <button className="ghost small-btn" disabled={testingAll} onClick={() => testMany(selRows)}>{testingAll ? <Spinner /> : null}Test</button>
        <button className="ghost small-btn" disabled={!!busy} onClick={() => bulk("Paused retrieval", { action: "pause" })}>{busyIcon("Paused retrieval", "pause")}Pause retrieval</button>
        <button className="ghost small-btn" disabled={!!busy} onClick={() => bulk("Resumed retrieval", { action: "resume" })}>{busyIcon("Resumed retrieval", "refresh")}Resume retrieval</button>
        <span className="bulk-group">
          <select aria-label="API to pause or resume" value={bulkCat} onChange={e => setBulkCat(e.target.value)}>{CATS.map(c => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}</select>
          <button className="ghost small-btn" disabled={!!busy} onClick={() => bulk(`Paused ${CAT_LABEL[bulkCat]}`, { action: "pause_endpoint", category: bulkCat })}>Pause</button>
          <button className="ghost small-btn" disabled={!!busy} onClick={() => bulk(`Resumed ${CAT_LABEL[bulkCat]}`, { action: "resume_endpoint", category: bulkCat })}>Resume</button>
        </span>
        <span className="bulk-group">
          <select aria-label="New status" value={bulkStatus} onChange={e => setBulkStatus(e.target.value)}>
            <option value="live">Live</option><option value="onboarding">Onboarding</option><option value="suspended">Suspended</option></select>
          <button className="ghost small-btn" disabled={!!busy} onClick={() => bulk(`Status set to ${bulkStatus}`, { action: "set_status", value: bulkStatus })}>Set status</button>
        </span>
        <span className="bulk-group">
          <input aria-label="Region" list="region-list" placeholder="Region" value={bulkRegion} onChange={e => setBulkRegion(e.target.value)} />
          <button className="ghost small-btn" disabled={!!busy} onClick={() => bulk(bulkRegion.trim() ? `Region set to ${bulkRegion.trim()}` : "Region cleared", { action: "set_region", value: bulkRegion.trim() })}>{bulkRegion.trim() ? "Set region" : "Clear region"}</button>
        </span>
      </BulkBar>
      <datalist id="region-list">{regions.filter(r => r.region !== NO_REGION).map(r => <option key={r.region} value={r.region} />)}</datalist>
      {note && <div className="okline card small" role="status">{note}</div>}
      {err && <div className="error" role="alert">{err}</div>}
      {toggleErr && <div className="error" role="alert">{toggleErr}</div>}
      <div className="table-wrap"><table>
        <thead><tr><th className="sel-col"><PageCheck ids={page.map(p => p.mnemonic)} sel={sel} onChange={setSel} label="Select every plant on this page" /></th>
          <th><span className="sr-only">Expand</span></th><th>Plant</th><th>Region</th><th>Retrieval on/off</th><th>Status</th><th className="num">Capacity</th><th>Endpoints retrieved</th><th>API key</th><th>Last test</th><th></th></tr></thead>
        <tbody>{!page.length && <tr><td colSpan={11} className="muted">{filtered ? "No plants match these filters." : "No plants yet. Add one, or import several, to start retrieval."}</td></tr>}{page.map(p => (<Fragment key={p.mnemonic}>
          <tr className={sel.has(p.mnemonic) ? "sel" : ""}>
            <td className="sel-col"><input type="checkbox" aria-label={`Select ${p.mnemonic.toUpperCase()}`} checked={sel.has(p.mnemonic)}
              onChange={e => setSel(s => { const n = new Set(s); e.target.checked ? n.add(p.mnemonic) : n.delete(p.mnemonic); return n; })} /></td>
            <td><button className="ghost icon" aria-label={`Endpoints of ${p.mnemonic.toUpperCase()}`} aria-expanded={!!open[p.mnemonic]}
              onClick={() => setOpen(o => ({ ...o, [p.mnemonic]: !o[p.mnemonic] }))}><Icon name="chevron" className="chev" /></button></td>
            <td><a href={`#/plant/${p.mnemonic}`} className="mnem">{p.mnemonic.toUpperCase()}</a><div className="small muted">{p.name}</div></td>
            <td className="small">{p.region || <span className="muted">—</span>}</td>
            <td><Switch on={p.polling_enabled} onChange={v => togglePlant(p, v)} title={`Retrieval for ${p.mnemonic.toUpperCase()}: pause or resume all its endpoints`} /></td>
            <td><span className={`badge ${p.status === "live" ? "green" : p.status === "onboarding" ? "amber" : ""}`}>{p.status}</span></td>
            <td className="num">{p.capacity_mw ? `${p.capacity_mw} MW` : "—"}</td>
            <td className="small">{CATS.filter(c => p.endpoints.some(e => e.category === c)).map(c => {
              const e = p.endpoints.find(x => x.category === c)!;
              return <span key={c} className={`chip ${e.polled ? "on-soft" : "off"}`} title={e.polled ? `Retrieved: ${e.url}` : `Not retrieved: ${e.off_reason}`}>{CAT_LABEL[c]}</span>;
            })}</td>
            <td><KeyBadge p={p} /></td>
            <td><PlantSummary p={p} tests={tests} /></td>
            <td className="actions">
              <button className="ghost" onClick={() => testPlant(p)} disabled={!p.endpoints.length}>Test all</button>
              <a className="button ghost" href={`#/admin/plants/${p.mnemonic}`}>Edit</a>
              <button className="ghost danger" onClick={() => setDeleting(p)}>Delete</button>
            </td>
          </tr>
          {open[p.mnemonic] && (
            <tr className="sub"><td></td><td></td><td colSpan={9}>
              {!p.endpoints.length ? <span className="muted small">No endpoints configured.</span> : (
                <table className="inner"><tbody>{CATS.filter(c => p.endpoints.some(e => e.category === c)).map(c => {
                  const e = p.endpoints.find(x => x.category === c)!;
                  return (
                    <tr key={c}>
                      <td className="ep-switch"><Switch on={e.enabled} onChange={v => toggleEndpoint(p, c, v)} title={`Retrieval for ${p.mnemonic.toUpperCase()} ${CAT_LABEL[c]}`} /></td>
                      <td><b>{CAT_LABEL[c]}</b><div className={`small ${e.polled ? "good" : "muted"}`}>{e.polled ? "Retrieved on schedule" : e.off_reason}</div></td>
                      <td className="mono small url-cell">{e.url}</td>
                      <td><Verdict r={tests[`${p.mnemonic}:${c}`]} /></td>
                      <td><button className="ghost small-btn" aria-label={`Test ${p.mnemonic.toUpperCase()} ${CAT_LABEL[c]}`} disabled={tests[`${p.mnemonic}:${c}`] === "running"} onClick={() => test(p.mnemonic, [c], c)}>Test</button></td>
                    </tr>
                  );
                })}</tbody></table>
              )}
            </td></tr>
          )}
        </Fragment>))}</tbody>
      </table></div>
      <Pager total={shown.length} offset={offset} limit={size} noun="plants" allowAll
        onOffset={o => setQ({ page: String(size ? Math.floor(o / size) : 0) })} onLimit={n => setQ({ size: String(n), page: "0" })} />
      {deleting && <DeleteDialog p={deleting} onClose={() => setDeleting(null)} onDone={() => { setDeleting(null); invalidatePlantIndex(); load(); }} />}
      {importing && <ImportDialog onClose={() => setImporting(false)} onDone={() => { invalidatePlantIndex(); load(); }} />}
    </>
  );
}

/** Minimal CSV/TSV parser for pasted spreadsheet rows (quoted fields, commas or tabs). */
function parseTable(text: string): Record<string, string>[] {
  const lines = text.replace(/\r/g, "").split("\n").filter(l => l.trim());
  if (lines.length < 2) return [];
  const sep = lines[0].includes("\t") ? "\t" : ",";
  const split = (l: string) => {
    const out: string[] = []; let cur = "", q = false;
    for (let i = 0; i < l.length; i++) {
      const ch = l[i];
      if (q) { if (ch === '"' && l[i + 1] === '"') { cur += '"'; i++; } else if (ch === '"') q = false; else cur += ch; }
      else if (ch === '"') q = true; else if (ch === sep) { out.push(cur); cur = ""; } else cur += ch;
    }
    out.push(cur);
    return out.map(x => x.trim());
  };
  const head = split(lines[0]).map(h => h.toLowerCase().replace(/[^a-z_]/g, "_").replace(/^_+|_+$/g, ""));
  const alias: Record<string, string> = { capacity: "capacity_mw", mw: "capacity_mw", base: "base_url", url: "base_url", stations: "mmf_station_count", mmf_stations: "mmf_station_count", plant: "mnemonic", code: "mnemonic" };
  return lines.slice(1).map(l => {
    const cells = split(l);
    return Object.fromEntries(head.map((h, i) => [alias[h] || h, cells[i] ?? ""]));
  });
}

type ImportResult = { dry_run: boolean; summary: Record<string, number>; rows: { line: number; mnemonic: string; action: string; message: string }[] };

function ImportDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [text, setText] = useState("mnemonic,name,region,capacity_mw,base_url\n");
  const [update, setUpdate] = useState(false);
  const [res, setRes] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const rows = useMemo(() => parseTable(text), [text]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [onClose]);
  const run = async (dry: boolean) => {
    setBusy(true); setErr("");
    try {
      const r = await api<ImportResult>("/admin/plants/import", { method: "POST", body: { rows, dry_run: dry, update_existing: update } });
      setRes(r);
      if (!dry) onDone();
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  const s = res?.summary;
  return (
    <div className="modal" onClick={() => !busy && onClose()}>
      <div className="card wide-modal" role="dialog" aria-modal="true" aria-labelledby="imp-title" onClick={e => e.stopPropagation()}>
        <h3 id="imp-title">Import plants</h3>
        <p className="small muted">Paste rows from a spreadsheet (tab-separated) or CSV with a header row. Columns: <code>mnemonic</code>, <code>name</code>, <code>base_url</code> (needed for new plants),
          and optionally <code>region</code>, <code>capacity_mw</code>, <code>mmf_station_count</code>, <code>status</code> (onboarding, live or suspended; default onboarding).
          Each new plant gets the five rev5 endpoints under its base URL, e.g. <code>{"{base_url}"}/{"{mnemonic}"}/forecast/rolling</code> and <code>/mmf/live</code>. Add API keys afterwards on each plant.</p>
        <textarea className="mono import-box" rows={8} value={text} aria-label="Plants to import" onChange={e => { setText(e.target.value); setRes(null); }} />
        <label className="toggle"><input type="checkbox" checked={update} onChange={e => { setUpdate(e.target.checked); setRes(null); }} /> Update plants that already exist (name, region, capacity, MMF stations, status; never URLs or keys)</label>
        {err && <div className="error" role="alert">{err}</div>}
        {res && s && (
          <div className="import-result">
            <p className="small" role="status"><b>{res.dry_run ? "Check" : "Done"}:</b> {s.create} to add, {s.update} to update, {s.skip} skipped, <span className={s.error ? "txt-red" : ""}>{s.error} with errors</span>{res.dry_run ? " (nothing saved yet)" : ""}.</p>
            <div className="table-wrap import-table"><table>
              <thead><tr><th className="num">Line</th><th>Plant</th><th>Result</th><th>Detail</th></tr></thead>
              <tbody>{res.rows.map(r => (
                <tr key={r.line}><td className="num">{r.line}</td><td className="mnem">{r.mnemonic.toUpperCase()}</td>
                  <td><span className={`badge ${r.action === "error" ? "red" : r.action === "skip" ? "" : "green"}`}>{r.action}</span></td><td className="small">{r.message}</td></tr>
              ))}</tbody>
            </table></div>
          </div>
        )}
        <div className="actions">
          <span className="small muted">{rows.length} row{rows.length === 1 ? "" : "s"} read</span>
          <button className="ghost push-right" onClick={onClose} disabled={busy}>{res && !res.dry_run ? "Close" : "Cancel"}</button>
          <button className="ghost" onClick={() => run(true)} disabled={busy || !rows.length}>{busy ? <Spinner /> : null}Check</button>
          <button onClick={() => run(false)} disabled={busy || !rows.length || !res?.dry_run || !!(res && (res.summary.create + res.summary.update) === 0)}
            title={!res?.dry_run ? "Check the rows first" : undefined}>{(() => { const n = res?.dry_run ? res.summary.create + res.summary.update : 0; return n ? `Import ${n} plant${n === 1 ? "" : "s"}` : "Import"; })()}</button>
        </div>
      </div>
    </div>
  );
}

function DeleteDialog({ p, onClose, onDone }: { p: PlantRow; onClose: () => void; onDone: () => void }) {
  const [usage, setUsage] = useState<any>(null);
  const [typed, setTyped] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api(`/admin/plants/${p.mnemonic}/usage`).then(setUsage); }, [p.mnemonic]);
  const busyRef = useRef(false); busyRef.current = busy;
  useEffect(() => {
    // Escape closes the dialog; focus goes back to the button that opened it.
    const prev = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !busyRef.current) onClose(); };
    addEventListener("keydown", onKey);
    return () => { removeEventListener("keydown", onKey); prev?.focus?.(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const del = async () => {
    setBusy(true); setErr("");
    try { await api(`/admin/plants/${p.mnemonic}?confirm=${encodeURIComponent(typed)}`, { method: "DELETE" }); onDone(); return; }
    catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  return (
    <div className="modal" onClick={() => !busy && onClose()}>
      <div className="card" role="alertdialog" aria-modal="true" aria-labelledby="del-title" onClick={e => e.stopPropagation()}>
        <h3 id="del-title">Delete {p.mnemonic.toUpperCase()}?</h3>
        <p>This permanently removes the plant, its endpoints and key, and all of its stored history
          {usage ? <>: <b>{usage.polls}</b> submissions, <b>{usage.snapshots}</b> forecast snapshots and <b>{usage.alerts}</b> alerts{usage.users ? <>, and its access for <b>{usage.users}</b> LSS user(s)</> : null}</> : null}.
          Raw responses kept as PPA evidence go too.</p>
        <p className="muted small">To stop retrieval but keep the history, edit the plant and set its status to <b>suspended</b> instead.</p>
        <label className="field">Type <b>{p.mnemonic.toUpperCase()}</b> to confirm
          <input value={typed} onChange={e => setTyped(e.target.value)} autoFocus /></label>
        {err && <div className="error" role="alert">{err}</div>}
        <div className="actions">
          <button className="ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="danger-fill push-right" disabled={busy || typed.toLowerCase() !== p.mnemonic} onClick={del}>{busy ? <><Spinner />Deleting…</> : "Delete plant and history"}</button>
        </div>
      </div>
    </div>
  );
}

const blankEndpoints = (): Endpoint[] => CATS.map(c => ({ category: c, url: "", enabled: true, note: null }));

export function PlantForm({ mnem }: { mnem?: string }) {
  const editing = !!mnem;
  const [f, setF] = useState<any>(null);
  const [base, setBase] = useState("");
  const [key, setKey] = useState("");
  const [removeKey, setRemoveKey] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const { tests, test, err: testErr } = useTests();
  const index = usePlantIndex();
  const regions = [...new Set((index || []).map(p => p.region).filter(Boolean) as string[])].sort();

  useEffect(() => {
    if (!editing) {
      setF({ mnemonic: "", name: "", region: "", capacity_mw: "", mmf_station_count: "", status: "onboarding", notes: "",
        auth_method: "none", auth_param_name: "", has_api_key: false, api_key_hint: null, endpoints: blankEndpoints() });
      return;
    }
    api<PlantRow>(`/plants/${mnem}`).then(p => {
      const eps = blankEndpoints().map(b => {
        const e = p.endpoints.find(x => x.category === b.category);
        return e ? { ...e } : { ...b, enabled: false };
      });
      setF({ ...p, region: p.region ?? "", capacity_mw: p.capacity_mw ?? "", mmf_station_count: p.mmf_station_count ?? "", notes: p.notes ?? "",
        auth_param_name: p.auth_param_name ?? "", endpoints: eps });
    }).catch(e => setErr(e.message));
  }, [mnem]);

  if (!f) return err ? <div className="error" role="alert">{err}</div> : <Skeleton rows={6} />;
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const setEp = (i: number, k: string, v: any) => set("endpoints", f.endpoints.map((e: Endpoint, j: number) => (j === i ? { ...e, [k]: v } : e)));
  const fillFromBase = () => {
    const b = base.trim().replace(/\/+$/, "");
    const m = f.mnemonic.trim().toLowerCase();
    if (!b || !m) { setErr("Enter the mnemonic and a base URL first"); return; }
    setErr("");
    set("endpoints", f.endpoints.map((e: Endpoint) => ({ ...e, url: `${b}/${m}/${DEFAULT_PATHS[e.category]}` })));
  };

  const save = async () => {
    setErr(""); setSaving(true);
    const body: any = {
      mnemonic: f.mnemonic.trim(), name: f.name.trim(), status: f.status, notes: f.notes || null, region: f.region.trim() || null,
      capacity_mw: f.capacity_mw === "" ? null : Number(f.capacity_mw),
      mmf_station_count: f.mmf_station_count === "" ? null : Number(f.mmf_station_count),
      auth_method: f.auth_method, auth_param_name: f.auth_method === "none" ? null : (f.auth_param_name.trim() || null),
      endpoints: f.endpoints.filter((e: Endpoint) => e.url.trim()).map((e: Endpoint) => ({ ...e, url: e.url.trim() })),
    };
    if (f.auth_method === "none") body.api_key = f.has_api_key ? "" : null;
    else if (removeKey) body.api_key = "";
    else if (key) body.api_key = key;
    try {
      const saved = await api(editing ? `/admin/plants/${mnem}` : "/admin/plants", { method: editing ? "PUT" : "POST", body });
      invalidatePlantIndex();
      location.hash = editing ? "#/admin/plants" : `#/admin/plants/${saved.mnemonic}`;
      if (!editing) return;
    } catch (e: any) { setErr(e.message); }
    setSaving(false);
  };

  const savedCats: string[] = editing ? f.endpoints.filter((e: Endpoint) => e.url.trim()).map((e: Endpoint) => e.category) : [];
  const testing = savedCats.some(c => tests[`${mnem}:${c}`] === "running");

  const keyPlaceholder = f.has_api_key ? `Leave blank to keep the stored key (••••${f.api_key_hint || ""})` : "Paste the plant's API key";
  return (
    <>
      <div className="title-row">
        <h2 className="crumbs"><a href="#/admin/plants">Plants</a><span className="sep" aria-hidden="true">/</span>{editing ? mnem!.toUpperCase() : "New plant"}</h2>
      </div>
      <div className="form-grid">
        <section className="card">
          <h3>Plant</h3>
          <div className="row2">
            <label className="field">Mnemonic
              <input value={editing ? f.mnemonic.toUpperCase() : f.mnemonic} disabled={editing} maxLength={8} placeholder="e.g. KMLS"
                onChange={e => set("mnemonic", e.target.value.replace(/[^A-Za-z0-9]/g, ""))} /></label>
            <label className="field">Name<input value={f.name} onChange={e => set("name", e.target.value)} placeholder="Plant name" /></label>
          </div>
          <div className="row3">
            <label className="field">Capacity (MW)<input type="number" min={0} step="0.1" value={f.capacity_mw} onChange={e => set("capacity_mw", e.target.value)} /></label>
            <label className="field">MMF stations<input type="number" min={0} value={f.mmf_station_count} onChange={e => set("mmf_station_count", e.target.value)} /></label>
            <label className="field">Status
              <select value={f.status} onChange={e => set("status", e.target.value)}>
                <option value="onboarding">Onboarding (not retrieved)</option><option value="live">Live (retrieved)</option><option value="suspended">Suspended (not retrieved)</option>
              </select></label>
          </div>
          <div className="row2">
            <label className="field">Region <span className="muted small">(groups and filters the fleet)</span>
              <input value={f.region} list="plant-regions" maxLength={60} placeholder="e.g. Northern" onChange={e => set("region", e.target.value)} /></label>
            <label className="field">Notes<input value={f.notes} onChange={e => set("notes", e.target.value)} /></label>
          </div>
          <datalist id="plant-regions">{regions.map(r => <option key={r} value={r} />)}</datalist>
        </section>

        <section className="card">
          <h3>API key <span className="muted small">(optional)</span></h3>
          <div className="row2">
            <label className="field">Send the key as
              <select value={f.auth_method} onChange={e => set("auth_method", e.target.value)}>
                {Object.entries(AUTH_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select></label>
            {f.auth_method !== "none" && (
              <label className="field">{f.auth_method === "header" ? "Header name" : "Parameter name"}
                <input value={f.auth_param_name} placeholder={f.auth_method === "header" ? "X-API-Key" : "apiKey"}
                  onChange={e => set("auth_param_name", e.target.value)} /></label>
            )}
          </div>
          {f.auth_method !== "none" && (
            <>
              <label className="field">Key
                <div className="inline">
                  <input type={showKey ? "text" : "password"} value={key} autoComplete="new-password" disabled={removeKey}
                    placeholder={keyPlaceholder} onChange={e => setKey(e.target.value)} />
                  <button type="button" className="ghost" aria-pressed={showKey} aria-label={showKey ? "Hide key" : "Show key"} onClick={() => setShowKey(!showKey)}>{showKey ? "Hide" : "Show"}</button>
                </div></label>
              {f.has_api_key && (
                <label className="toggle"><input type="checkbox" checked={removeKey} onChange={e => setRemoveKey(e.target.checked)} /> Remove the stored key</label>
              )}
              <p className="muted small">Stored encrypted. It is sent on every call to this plant
                {f.auth_method === "header" ? ` as the ${f.auth_param_name || "X-API-Key"} header` : ` as ?${f.auth_param_name || "apiKey"}=…`}, and never shown again after saving.
                {f.auth_method === "query" && " A header is safer: query strings can end up in the plant's server logs."}</p>
            </>
          )}
          {f.auth_method === "none" && f.has_api_key && <p className="muted small">Saving with None removes the stored key.</p>}
        </section>

        <section className="card wide">
          <h3>Endpoints</h3>
          <div className="inline base">
            <input aria-label="Base URL" placeholder="Base URL, e.g. https://ws3.gedine.work" value={base} onChange={e => setBase(e.target.value)} />
            <button type="button" className="ghost" onClick={fillFromBase}>Fill all from base URL</button>
          </div>
          <p className="muted small">Each endpoint has its own full URL, because plants don't all follow the rev5 paths. Leave a URL empty if the plant doesn't serve it. Untick Retrieve to keep the URL but skip it.</p>
          <div className="table-wrap"><table>
            <thead><tr><th>Endpoint</th><th>URL</th><th>Retrieve</th><th>Note</th>{editing && <th>Test (saved settings)</th>}</tr></thead>
            <tbody>{f.endpoints.map((e: Endpoint, i: number) => (
              <tr key={e.category}>
                <td><b>{CAT_LABEL[e.category]}</b></td>
                <td><input className="url" aria-label={`${CAT_LABEL[e.category]} URL`} value={e.url} placeholder={`https://…/${f.mnemonic.toLowerCase() || "mnem"}/${DEFAULT_PATHS[e.category]}`}
                  onChange={x => setEp(i, "url", x.target.value)} /></td>
                <td><input type="checkbox" aria-label={`Retrieve ${CAT_LABEL[e.category]}`} checked={e.enabled} onChange={x => setEp(i, "enabled", x.target.checked)} /></td>
                <td><input aria-label={`${CAT_LABEL[e.category]} note`} value={e.note ?? ""} onChange={x => setEp(i, "note", x.target.value || null)} /></td>
                {editing && <td className="test-cell">{e.url.trim() ? (
                  <div className="inline top">
                    <button className="ghost small-btn" disabled={tests[`${mnem}:${e.category}`] === "running"} onClick={() => test(mnem!, [e.category], e.category)}>Test</button>
                    <Verdict r={tests[`${mnem}:${e.category}`]} />
                  </div>) : <span className="muted small">—</span>}</td>}
              </tr>
            ))}</tbody>
          </table></div>
        </section>
      </div>
      {err && <div className="error" role="alert">{err}</div>}
      <div className="actions bar">
        <button onClick={save} disabled={saving}>{saving ? <><Spinner />Saving…</> : editing ? "Save changes" : "Add plant"}</button>
        {editing && <button className="ghost" onClick={() => test(mnem!, savedCats)} disabled={testing || !savedCats.length}>{testing ? <><Spinner />Testing…</> : "Test all endpoints"}</button>}
        <a className="button ghost" href="#/admin/plants">Cancel</a>
      </div>
      {editing && <p className="muted small">Tests use the saved URL and key, so save first if you changed anything.</p>}
      {testErr && <div className="error" role="alert">{testErr}</div>}
    </>
  );
}
