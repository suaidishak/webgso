import { Fragment, useEffect, useState } from "react";
import { api, CATS, CAT_LABEL, fmt } from "../api";
import Icon, { Skeleton, Spinner } from "../Icon";
import { BulkBar, NO_REGION, PageCheck, Pager, useDebounced, useQueryState } from "../components";

type Cell = { state: string; attempt_id: number | null; off_reason: string | null; last_poll: string | null; http_status: number | null; latency_ms: number | null;
  major_flags: number; minor_flags: number; error: string | null; last_valid: string | null; open_alerts: number };
type Row = { mnemonic: string; name: string; region: string | null; status: string; polling_enabled: boolean; is_simulated?: boolean; protocols?: string[];
  cells: Record<string, Cell>; open_alerts: number; problem_count: number; last_poll: string | null };
type Group = { region: string | null; plants: number; problems: number; open_alerts: number };
type Board = {
  generated_at: string; plants: Row[]; total: number; matched: number; counts: Record<string, number>; open_alerts: number;
  facets: Record<string, number>; regions: { region: string | null; plants: number; problems: number }[];
  groups: Group[]; mnemonics: string[]; polling?: { fleet: boolean; types: Record<string, boolean> };
  sim_counts?: { real: number; simulated: number };
};

export const STATE_LABEL: Record<string, string> = {
  ok: "Compliant", minor: "Minor issues", invalid: "Non-compliant", down: "Unreachable", never: "Not retrieved yet", disabled: "Paused",
};

export function Legend() {
  return <div className="legend">{Object.entries(STATE_LABEL).map(([k, v]) => <span key={k}><i className={`dot ${k}`} aria-hidden="true" />{v}</span>)}</div>;
}

// Filter chips on the board: plants matching each state (any API, or the chosen one).
const STATE_FILTERS: [string, string][] = [
  ["", "All"], ["problems", "With problems"], ["down", "Unreachable"], ["invalid", "Non-compliant"], ["alerts", "Open alerts"],
  ["minor", "Minor issues"], ["never", "Not retrieved yet"], ["disabled", "Paused"], ["healthy", "Healthy"],
];
const SORTS: [string, string][] = [
  ["problems", "Problems first"], ["alerts", "Most open alerts"], ["last_poll", "Stalest submission first"], ["mnemonic", "Mnemonic A–Z"],
  ["name", "Name A–Z"], ["region", "Region"], ["capacity", "Largest capacity"],
];
// sim: simulated (fictitious) plants are hidden unless asked for.
const DEFAULTS = { q: "", state: "", api: "", region: "", status: "", sort: "problems", group: "", density: "detailed", size: "50", page: "0", sim: "real" };

export default function Fleet({ isAdmin = false, query = "" }: { isAdmin?: boolean; query?: string }) {
  const [f, set] = useQueryState("/", query, DEFAULTS);
  const [data, setData] = useState<Board | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState("");
  const [note, setNote] = useState("");
  const [tick, setTick] = useState(0);
  const q = useDebounced(f.q);
  const size = Number(f.size) || 0;
  const offset = Number(f.page) * (size || 0);
  const setFilter = (patch: Partial<typeof DEFAULTS>) => set({ ...patch, page: "0" });

  useEffect(() => {
    let alive = true;
    const p = new URLSearchParams({ sort: f.sort, offset: String(offset), limit: String(size) });
    if (q.trim()) p.set("q", q.trim());
    if (f.state) p.set("state", f.state);
    if (f.api) p.set("category", f.api);
    if (f.region) p.set("region", f.region);
    if (f.status) p.set("status", f.status);
    if (f.group) p.set("group", f.group);
    p.set("sim", f.sim || "real");
    const load = () => {
      setLoading(true);
      return api<Board>(`/fleet/status?${p}`).then(d => { if (alive) { setData(d); setErr(""); } })
        .catch(e => alive && setErr(e.message)).finally(() => alive && setLoading(false));
    };
    load();
    const t = setInterval(load, 30000);
    return () => { alive = false; clearInterval(t); };
  }, [q, f.state, f.api, f.region, f.status, f.sort, f.group, f.sim, size, offset, tick]);

  if (!data) return err ? <div className="error" role="alert">{err}</div> : <Skeleton tiles rows={8} />;
  const counts = data.counts;
  const problemPlants = data.regions.reduce((a, r) => a + r.problems, 0);
  const filtered = !!(f.q || f.state || f.api || f.region || f.status);
  const compact = f.density === "compact";
  const grouped = f.group === "region";
  const pageIds = data.plants.map(p => p.mnemonic);
  const groupOf = (r: { region: string | null }) => r.region || NO_REGION;
  const groupInfo = new Map(data.groups.map(g => [g.region || NO_REGION, g]));

  const bulk = async (action: "pause" | "resume") => {
    setBulkBusy(action); setNote("");
    try {
      const r = await api("/admin/plants/bulk", { method: "POST", body: { plants: [...sel], action } });
      setNote(`${action === "pause" ? "Paused" : "Resumed"} retrieval for ${r.changed} plant${r.changed === 1 ? "" : "s"}${r.unchanged ? ` (${r.unchanged} already ${action === "pause" ? "paused" : "on"})` : ""}.`);
      setTick(t => t + 1);
    } catch (e: any) { setErr(e.message); }
    setBulkBusy("");
  };
  const tile = (key: string, value: number, label: string, cls: string, state: string, hint: string) => (
    <button key={key} className={`tile ${cls}${value ? "" : " zero"}${f.state === state && state ? " picked" : ""}`} aria-pressed={f.state === state && !!state}
      title={hint} onClick={() => setFilter({ state: f.state === state ? "" : state })}><b>{value}</b><span>{label}</span></button>
  );
  const nCols = CATS.length + 2 + (isAdmin ? 1 : 0) + (grouped ? 0 : 1);

  return (
    <>
      <div className="title-row">
        <h2>Fleet compliance</h2>
        <span className="muted small live" role="status">Updated {fmt(data.generated_at)} MYT · refreshes every 30 s{loading ? " · loading…" : ""}</span>
        {isAdmin && <a className="button ghost push-right" href="#/admin/poll/all/all/run"><Icon name="zap" />Retrieve whole fleet now</a>}
      </div>
      {err && <div className="error" role="alert">Couldn't refresh: {err}. Showing the last data received.</div>}
      <div className="tiles">
        {tile("plants", data.total, "Plants", "", "", "Show every plant")}
        {tile("problems", problemPlants, "Problem plants", "invalid", "problems", "Plants with an unreachable or non-compliant API, or an open alert")}
        <div className="tile ok"><b>{(counts.ok || 0) + (counts.minor || 0)}</b><span>Endpoints valid</span></div>
        {tile("invalid", counts.invalid || 0, "Non-compliant", "invalid", "invalid", "Endpoints breaking a major rule. Click to show those plants")}
        {tile("down", counts.down || 0, "Unreachable", "down", "down", "Endpoints not answering or answering with an error. Click to show those plants")}
        {tile("alerts", data.open_alerts ?? 0, "Open alerts", "alerts", "alerts", `${data.facets.alerts ?? 0} plants have open alerts. Click to show them`)}
      </div>
      {data.polling && !data.polling.fleet && <div className="banner" role="status"><Icon name="pause" />Fleet retrieval is paused. No plant is being retrieved on schedule. An admin can resume it on the Plants page.</div>}
      {data.polling && data.polling.fleet && Object.entries(data.polling.types).some(([, v]) => !v) && (
        <div className="banner soft" role="status"><Icon name="pause" />Paused for all plants: {Object.entries(data.polling.types).filter(([, v]) => !v).map(([k]) => CAT_LABEL[k]).join(", ")}.</div>
      )}

      <div className="card board-tools">
        <div className="board-row">
          <input className="search" type="search" placeholder="Search plant, name or region" aria-label="Search plants"
            value={f.q} onChange={e => setFilter({ q: e.target.value })} />
          <label className="field inline-field"><span>API</span>
            <select value={f.api} onChange={e => setFilter({ api: e.target.value })}>
              <option value="">Any API</option>{CATS.map(c => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}
            </select></label>
          {data.regions.length > 1 && (
            <label className="field inline-field"><span>Region</span>
              <select value={f.region} onChange={e => setFilter({ region: e.target.value })}>
                <option value="">All regions</option>
                {data.regions.map(r => <option key={r.region || NO_REGION} value={r.region || NO_REGION}>{r.region || NO_REGION} ({r.plants}{r.problems ? `, ${r.problems} with problems` : ""})</option>)}
              </select></label>
          )}
          <label className="field inline-field"><span>Plant status</span>
            <select value={f.status} onChange={e => setFilter({ status: e.target.value })}>
              <option value="">Any</option><option value="live">Live</option><option value="onboarding">Onboarding</option><option value="suspended">Suspended</option>
            </select></label>
          {!!data.sim_counts?.simulated && (
            <label className="field inline-field"><span>Plants</span>
              <select value={f.sim} onChange={e => setFilter({ sim: e.target.value })}>
                <option value="real">Real ({data.sim_counts.real})</option>
                <option value="simulated">Simulated ({data.sim_counts.simulated})</option>
                <option value="all">Real and simulated</option>
              </select></label>
          )}
          <label className="field inline-field"><span>Sort</span>
            <select value={f.sort} onChange={e => setFilter({ sort: e.target.value })}>
              {SORTS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select></label>
          <label className="check-field"><input type="checkbox" checked={grouped} onChange={e => setFilter({ group: e.target.checked ? "region" : "" })} />Group by region</label>
          <div className="seg" role="group" aria-label="Density">
            <button className={compact ? "" : "on"} aria-pressed={!compact} onClick={() => set({ density: "detailed" })}>Detailed</button>
            <button className={compact ? "on" : ""} aria-pressed={compact} onClick={() => set({ density: "compact" })}>Compact</button>
          </div>
        </div>
        <div className="chips state-chips" role="group" aria-label={`Show plants${f.api ? ` by their ${CAT_LABEL[f.api]} API` : ""}`}>
          {STATE_FILTERS.map(([k, label]) => (
            <label key={k || "all"} className={`chip ${f.state === k ? "on" : ""}`}>
              <input type="radio" name="state" checked={f.state === k} onChange={() => setFilter({ state: k })} />
              {k && k !== "problems" && k !== "alerts" && k !== "healthy" && <i className={`dot ${k}`} aria-hidden="true" />}{label}
              <span className="chip-n">{data.facets[k || "all"] ?? 0}</span>
            </label>
          ))}
          {filtered && <button className="ghost small-btn" onClick={() => set({ ...DEFAULTS, sort: f.sort, group: f.group, density: f.density, size: f.size, sim: f.sim })}><Icon name="x" size={12} />Clear filters</button>}
        </div>
      </div>

      <div className="board-meta">
        <span className="small muted" role="status">
          {data.matched === data.total ? `${data.total} plants` : `${data.matched} of ${data.total} plants match`}
          {f.api ? ` · states shown for ${CAT_LABEL[f.api]}` : ""}
          {f.sim === "real" && !!data.sim_counts?.simulated && <> · {data.sim_counts.simulated} simulated plants hidden <button className="ghost small-btn" onClick={() => setFilter({ sim: "all" })}>Show them</button></>}
          {f.sim === "simulated" && <> · showing simulated (fictitious) plants only</>}
        </span>
        <Legend />
      </div>

      {isAdmin && (
        <BulkBar count={sel.size} matched={data.matched} onSelectAll={() => setSel(new Set(data.mnemonics))} onClear={() => setSel(new Set())}>
          <a className="button small-btn" href={`#/admin/poll/${[...sel].join(",")}/all/run`}><Icon name="zap" size={14} />Retrieve now</a>
          <button className="ghost small-btn" disabled={!!bulkBusy} onClick={() => bulk("pause")}>{bulkBusy === "pause" ? <Spinner /> : <Icon name="pause" size={14} />}Pause retrieval</button>
          <button className="ghost small-btn" disabled={!!bulkBusy} onClick={() => bulk("resume")}>{bulkBusy === "resume" ? <Spinner /> : <Icon name="refresh" size={14} />}Resume retrieval</button>
          <a className="button ghost small-btn" href={`#/reports?plants=${[...sel].join(",")}&latest=1`}><Icon name="table" size={14} />Reports</a>
          <a className="button ghost small-btn" href={`#/alerts?plants=${[...sel].join(",")}`}><Icon name="bell" size={14} />Alerts</a>
        </BulkBar>
      )}
      {note && <div className="okline card small" role="status">{note}</div>}

      {!data.plants.length ? (
        <div className="card empty"><p className="muted">{filtered ? "No plants match these filters." : f.sim === "real" && data.sim_counts?.simulated ? "No real plants yet. Simulated plants are hidden." : "No plants yet."}</p>
          {!filtered && f.sim === "real" && !!data.sim_counts?.simulated && <button className="ghost" onClick={() => setFilter({ sim: "all" })}>Show simulated plants</button>}
          {filtered && <button className="ghost" onClick={() => set({ ...DEFAULTS, sort: f.sort, density: f.density })}>Clear filters</button>}</div>
      ) : (
        <div className="table-wrap">
          <table className={`board${compact ? " compact" : ""}`}>
            <thead><tr>
              {isAdmin && <th scope="col" className="sel-col"><PageCheck ids={pageIds} sel={sel} onChange={setSel} label="Select every plant on this page" /></th>}
              <th scope="col">Plant</th>
              {!grouped && <th scope="col">Region</th>}
              {CATS.map(c => <th key={c} scope="col" className={f.api === c ? "col-focus" : ""}>{CAT_LABEL[c]}</th>)}
              <th scope="col" className="num">Alerts</th>
            </tr></thead>
            <tbody>
              {data.plants.map((p, i) => {
                const g = groupOf(p);
                const head = grouped && (i === 0 || groupOf(data.plants[i - 1]) !== g);
                const info = groupInfo.get(g);
                const hidden = grouped && collapsed.has(g);
                return (
                  <Fragment key={p.mnemonic}>
                    {head && (
                      <tr className="group-row"><th colSpan={nCols} scope="rowgroup">
                        <button className="ghost small-btn" aria-expanded={!collapsed.has(g)} onClick={() => setCollapsed(s => { const n = new Set(s); n.has(g) ? n.delete(g) : n.add(g); return n; })}>
                          <Icon name="chevron" size={14} className="chev" /><b>{g}</b></button>
                        {info && <span className="small muted">{info.plants} plant{info.plants === 1 ? "" : "s"}
                          {info.problems ? <> · <span className="txt-red">{info.problems} with problems</span></> : " · all healthy"}
                          {info.open_alerts ? ` · ${info.open_alerts} open alerts` : ""}</span>}
                        {isAdmin && <button className="ghost small-btn push-right" onClick={() => {
                          const ids = data.plants.filter(x => groupOf(x) === g).map(x => x.mnemonic);
                          setSel(s => { const n = new Set(s); const all = ids.every(m => n.has(m)); ids.forEach(m => (all ? n.delete(m) : n.add(m))); return n; });
                        }}>Select {g} on this page</button>}
                      </th></tr>
                    )}
                    {!hidden && (
                      <tr className={sel.has(p.mnemonic) ? "sel" : ""}>
                        {isAdmin && <td className="sel-col"><input type="checkbox" aria-label={`Select ${p.mnemonic.toUpperCase()}`} checked={sel.has(p.mnemonic)}
                          onChange={e => setSel(s => { const n = new Set(s); e.target.checked ? n.add(p.mnemonic) : n.delete(p.mnemonic); return n; })} /></td>}
                        <th scope="row">
                          <a href={`#/plant/${p.mnemonic}`} className="mnem" title={p.name}>{p.mnemonic.toUpperCase()}</a>
                          {!compact && p.name && p.name.toLowerCase() !== p.mnemonic && <div className="small muted plant-name">{p.name}</div>}
                          {p.is_simulated && <span className="badge sim" title="Simulated plant: fictitious, served by the plant simulator">SIM</span>}
                          {p.protocols?.includes("soap") && <span className="badge proto small-badge" title="Uses SOAP web services">SOAP</span>}
                          {p.status !== "live" && <span className="badge amber small-badge">{p.status}</span>}
                          {!compact && isAdmin && <a className="poll-plant small" href={`#/admin/poll/${p.mnemonic}/all/run`} title={`Retrieve every ${p.mnemonic.toUpperCase()} API now and show the raw JSON`}>Retrieve now</a>}
                        </th>
                        {!grouped && <td className="small muted nowrap">{p.region || "—"}</td>}
                        {CATS.map(c => {
                          const cell = p.cells[c];
                          if (!cell) return <td key={c} className="muted">—</td>;
                          const tip = `${p.mnemonic.toUpperCase()} ${CAT_LABEL[c]}: ${STATE_LABEL[cell.state]}\nLast retrieved ${fmt(cell.last_poll)}\nLast valid ${fmt(cell.last_valid)}${cell.off_reason ? "\n" + cell.off_reason : ""}${cell.error ? "\n" + cell.error : ""}${cell.attempt_id ? "\nClick to see what in the response caused this" : ""}`;
                          return (
                            <td key={c} className={f.api === c ? "col-focus" : ""}>
                              <a href={cell.attempt_id ? `#/attempt/${cell.attempt_id}` : `#/plant/${p.mnemonic}`} className={`cell ${cell.state}`} title={tip}
                                aria-label={compact ? `${CAT_LABEL[c]}: ${STATE_LABEL[cell.state]}` : undefined}>
                                <i className={`dot ${cell.state}`} aria-hidden="true" />
                                {compact ? <small>{cell.last_poll ? fmt(cell.last_poll).slice(11) : "—"}{cell.major_flags ? ` · ${cell.major_flags}` : ""}</small> : <>
                                  <span>{STATE_LABEL[cell.state]}</span>
                                  <small>{cell.last_poll ? fmt(cell.last_poll).slice(11) : ""}{cell.major_flags ? ` · ${cell.major_flags} major` : ""}{cell.minor_flags ? ` · ${cell.minor_flags} minor` : ""}</small>
                                </>}
                              </a>
                            </td>
                          );
                        })}
                        <td className="num">{p.open_alerts ? <a href={`#/alerts?plants=${p.mnemonic}`} className="badge red" aria-label={`${p.open_alerts} open alerts`}>{p.open_alerts}</a> : <span className="muted">0</span>}</td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <Pager total={data.matched} offset={offset} limit={size} noun="plants" allowAll sizes={[25, 50, 100, 250]}
        onOffset={o => set({ page: String(size ? Math.floor(o / size) : 0) })} onLimit={n => set({ size: String(n), page: "0" })} />
    </>
  );
}
