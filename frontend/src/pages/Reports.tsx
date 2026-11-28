import { useEffect, useMemo, useState } from "react";
import { api, download, CATS, CAT_LABEL } from "../api";
import Icon, { Skeleton, Spinner } from "../Icon";
import Sheet, { SheetCol, cellText } from "../Sheet";
import { PlantPicker } from "../components";
import ReportTemplate from "./ReportTemplate";
import "./reports.css";

/* Reports. Two formats:
   - Client template (default): the client's DDQ / MMF templates, viewed online and downloaded as Excel.
   - Table view: any selection of polled data as a spreadsheet-style table, and the same selection as CSV.
   The filters live in the URL (#/reports?plants=bdws&category=dayahead&range=24h...), so a report can be
   bookmarked or shared, and other pages link here with filters already set. */

type Page = { columns: SheetCol[]; rows: Record<string, any>[]; total: number; polls: number; truncated: boolean };

const VIEWS = [
  { id: "points", label: "Forecast points", hint: "One row per 15-minute forecast slot." },
  { id: "wide", label: "Forecast by slot", hint: "Like a worksheet: one row per 15-minute slot, one column per plant poll, so plants sit side by side. Latest poll only keeps one column per plant and API." },
  { id: "mmf", label: "MMF readings", hint: "One row per station and parameter." },
  { id: "json", label: "As received", hint: "The JSON exactly as the plant sent it, flattened: one row per record, field names as sent." },
  { id: "polls", label: "Poll log", hint: "One row per poll: HTTP status, verdict and flag counts." },
] as const;

const RANGES: Record<string, { label: string; hours?: number; today?: boolean }> = {
  any: { label: "Any time" },
  "1h": { label: "Last hour", hours: 1 },
  today: { label: "Today", today: true },
  "24h": { label: "Last 24 hours", hours: 24 },
  "7d": { label: "Last 7 days", hours: 24 * 7 },
  "30d": { label: "Last 30 days", hours: 24 * 30 },
  custom: { label: "Custom range" },
};
const PAGE_SIZES = [100, 250, 500, 1000];

/** Current Malaysia time as "YYYY-MM-DDTHH:MM" (stored poll times are naive Malaysia time). */
function myt(offsetHours = 0): string {
  const d = new Date(Date.now() - offsetHours * 3600_000);
  return d.toLocaleString("sv-SE", { timeZone: "Asia/Kuala_Lumpur", hour12: false }).replace(" ", "T").slice(0, 16);
}

type F = { fmt: "template" | "table"; view: string; plants: string[]; category: string; range: string; from: string; to: string;
  latest: boolean; attempt: string; run: string; cf: Record<string, string> };

function readQuery(q: string): F {
  const p = new URLSearchParams(q);
  const scoped = p.get("attempt") || p.get("run");
  const fmt = p.get("fmt") === "table" || (!p.get("fmt") && (p.get("view") || scoped)) ? "table" : "template";
  return {
    fmt,
    view: p.get("view") || "points",
    plants: (p.get("plants") || "").split(",").filter(Boolean),
    category: p.get("category") || "all",
    range: p.get("range") || (p.get("from") || p.get("to") ? "custom" : scoped ? "any" : "24h"),
    from: p.get("from") || "", to: p.get("to") || "",
    latest: p.get("latest") === "1",
    attempt: p.get("attempt") || "", run: p.get("run") || "",
    cf: (() => { try { return JSON.parse(p.get("cf") || "{}"); } catch { return {}; } })(),
  };
}

/** The URL of this report page (no defaults), so it stays short. */
function pageQuery(f: F): string {
  const p = new URLSearchParams();
  if (f.fmt === "table") p.set("fmt", "table");
  if (f.fmt === "table" && f.view !== "points") p.set("view", f.view);
  if (f.plants.length) p.set("plants", f.plants.join(","));
  if (f.category !== "all") p.set("category", f.category);
  if (f.range === "custom") { if (f.from) p.set("from", f.from); if (f.to) p.set("to", f.to); }
  else if (f.range !== "24h") p.set("range", f.range);
  if (f.latest) p.set("latest", "1");
  if (f.attempt) p.set("attempt", f.attempt);
  if (f.run) p.set("run", f.run);
  if (f.fmt === "table" && Object.keys(f.cf).length) p.set("cf", JSON.stringify(f.cf));
  return p.toString();
}

/** API query: presets are turned into a concrete from/to at request time. */
function apiQuery(f: F, extra: Record<string, string> = {}): string {
  const p = new URLSearchParams({ view: f.view, ...extra });
  if (f.plants.length) p.set("plants", f.plants.join(","));
  if (f.category !== "all") p.set("category", f.category);
  const r = RANGES[f.range];
  if (f.range === "custom") { if (f.from) p.set("from", f.from); if (f.to) p.set("to", f.to + (f.to.length === 16 ? ":59" : "")); }
  else if (r?.hours) p.set("from", myt(r.hours));
  else if (r?.today) p.set("from", myt().slice(0, 10) + "T00:00");
  if (f.latest) p.set("latest", "true");
  if (f.attempt) p.set("attempt", f.attempt);
  if (f.run) p.set("run", f.run);
  if (Object.keys(f.cf).length) p.set("cf", JSON.stringify(f.cf));
  return p.toString();
}

export default function Reports({ query }: { query: string }) {
  const [f, setF] = useState<F>(() => readQuery(query));
  const [sort, setSort] = useState<{ key: string; desc: boolean } | null>(null);
  const [size, setSize] = useState(100);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const set = (patch: Partial<F>) => { setF(x => ({ ...x, ...patch })); setOffset(0); };

  // Keep the address bar in step with the filters without adding a history entry for every click.
  useEffect(() => {
    const q = pageQuery(f);
    history.replaceState(null, "", `#/reports${q ? "?" + q : ""}`);
  }, [f]);
  // Sorting is per view, since the columns differ.
  useEffect(() => { setSort(null); }, [f.view]);

  const q = useMemo(() => apiQuery(f, {
    limit: String(size), offset: String(offset),
    ...(sort ? { sort: sort.key, dir: sort.desc ? "desc" : "asc" } : {}),
  }), [f, size, offset, sort]);

  useEffect(() => {
    if (f.fmt !== "table") return;
    let live = true;
    setLoading(true); setErr("");
    const t = setTimeout(() => {
      api<Page>(`/reports/rows?${q}`)
        .then(d => { if (live) setPage(d); })
        .catch(e => { if (live) { setErr(e.message); setPage(null); } })
        .finally(() => { if (live) setLoading(false); });
    }, 200);
    return () => { live = false; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, f.fmt]);

  const exportCsv = async () => {
    setBusy(true); setErr("");
    try {
      await download(`/reports/export.csv?${apiQuery(f, sort ? { sort: sort.key, dir: sort.desc ? "desc" : "asc" } : {})}`);
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };

  // Keep view and API compatible: MMF data only exists in the MMF view and vice versa.
  const setCategory = (c: string) => set({
    category: c,
    view: c === "mmf" && (f.view === "points" || f.view === "wide") ? "mmf" : c !== "mmf" && c !== "all" && f.view === "mmf" ? "points" : f.view,
  });
  const setView = (v: string) => set({
    view: v, cf: {},
    latest: v === "wide" && f.view !== "wide" && !f.attempt && !f.run ? true : f.latest,
    category: v === "mmf" && f.category !== "all" && f.category !== "mmf" ? "all"
      : (v === "points" || v === "wide") && f.category === "mmf" ? "all" : f.category,
  });
  const sortBy = (key: string) => {
    setSort(s => (s?.key === key ? (s.desc ? null : { key, desc: true }) : { key, desc: false }));
    setOffset(0);
  };

  const viewInfo = VIEWS.find(v => v.id === f.view)!;
  const isTpl = f.fmt === "template";
  // The template always shows one API: forecasts in the DDQ layout, MMF in the MMF workbook.
  const tplCat = f.category === "all" ? "dayahead" : f.category;
  const tplQuery = apiQuery({ ...f, category: tplCat, cf: {}, view: tplCat === "mmf" ? "mmf" : "points" });
  const apis: readonly string[] = isTpl ? CATS : f.view === "mmf" ? CATS.filter(c => c === "mmf") : f.view === "points" || f.view === "wide" ? CATS.filter(c => c !== "mmf") : CATS;
  const total = page?.total ?? 0;
  const last = Math.min(offset + size, total);

  return (
    <>
      <div className="title-row"><h2>Reports</h2>
        <div className="tabs fmt-tabs" role="tablist" aria-label="Report format">
          <button role="tab" aria-selected={isTpl} className={isTpl ? "on" : ""} onClick={() => set({ fmt: "template" })}>
            <Icon name="table" />Client template</button>
          <button role="tab" aria-selected={!isTpl} className={!isTpl ? "on" : ""} onClick={() => set({ fmt: "table" })}>
            <Icon name="list" />Table view</button>
        </div>
        <span className="muted small">{isTpl
          ? "The client's DDQ and MMF templates, online and as an Excel download."
          : "Polled data as a table, filtered by plant, API and poll time. Download exactly what you see as CSV."}</span>
      </div>

      <div className="card report-filters">
        <div className="report-row">
          <div className="field"><span>Plants</span><PlantPicker value={f.plants} onChange={v => set({ plants: v })} /></div>
          <label className="field"><span>API</span>
            <select value={isTpl ? tplCat : apis.includes(f.category) ? f.category : "all"} onChange={e => (isTpl ? set({ category: e.target.value }) : setCategory(e.target.value))}>
              {apis.length > 1 && !isTpl && <option value="all">{f.view === "points" || f.view === "wide" ? "All forecast APIs" : "All APIs"}</option>}
              {apis.map(c => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}
            </select>
          </label>
          <label className="field"><span>Poll time</span>
            <select value={f.range} onChange={e => set({ range: e.target.value, ...(e.target.value === "custom" && !f.from ? { from: myt(24), to: myt() } : {}) })}>
              {Object.entries(RANGES).map(([k, r]) => <option key={k} value={k}>{r.label}</option>)}
            </select>
          </label>
          {f.range === "custom" && <>
            <label className="field"><span>From (MYT)</span>
              <input type="datetime-local" value={f.from} max={f.to || undefined} onChange={e => set({ from: e.target.value })} /></label>
            <label className="field"><span>To (MYT)</span>
              <input type="datetime-local" value={f.to} min={f.from || undefined} onChange={e => set({ to: e.target.value })} /></label>
          </>}
          <label className="check-field">
            <input type="checkbox" checked={f.latest} onChange={e => set({ latest: e.target.checked })} />
            {isTpl ? <>Latest submission only <span className="muted small">(each plant's latest poll in the time range)</span></>
              : <>Latest poll only <span className="muted small">(per plant and API, within the time range)</span></>}
          </label>
        </div>
        {(f.attempt || f.run) && (
          <div className="scope-chips">
            <span className="muted small">Limited to</span>
            {f.attempt && <span className="chip on">Poll #{f.attempt}<a href={`#/attempt/${f.attempt}`} className="small">raw JSON</a>
              <button className="icon ghost" aria-label="Remove poll filter" onClick={() => set({ attempt: "" })}><Icon name="x" size={12} /></button></span>}
            {f.run && <span className="chip on">Poll now run #{f.run}<a href={`#/admin/poll/runs/${f.run}`} className="small">open run</a>
              <button className="icon ghost" aria-label="Remove run filter" onClick={() => set({ run: "" })}><Icon name="x" size={12} /></button></span>}
          </div>
        )}
      </div>

      {isTpl ? <ReportTemplate query={tplQuery} fileHint="report.xlsx" /> : <>
      <div className="report-bar">
        <div className="tabs" role="tablist" aria-label="Report view">
          {VIEWS.map(v => <button key={v.id} role="tab" aria-selected={v.id === f.view} className={v.id === f.view ? "on" : ""}
            title={v.hint} onClick={() => setView(v.id)}>{v.label}</button>)}
        </div>
        <span className="muted small report-count" role="status">
          {loading ? <><Spinner /> Loading…</> : page ? <>{total.toLocaleString()} row{total === 1 ? "" : "s"} from {page.polls.toLocaleString()} poll{page.polls === 1 ? "" : "s"}</> : null}
        </span>
        <button className="push-right" onClick={exportCsv} disabled={busy || !total}>{busy ? <><Spinner />Preparing CSV…</> : <><Icon name="download" />Download CSV</>}</button>
      </div>
      <p className="muted small report-hint">{viewInfo.hint} Times are Malaysia time (MYT).{page?.truncated && <b className="bad"> {f.view === "wide" ? "Only the first 300 polls are shown as columns." : "Only the first 2,000 polls are flattened in this view."} Narrow the time range or plants, or tick Latest poll only, to see the rest.</b>}</p>

      {err && <div className="error" role="alert">{err}</div>}
      {!page && loading && <Skeleton rows={8} />}
      {page && (total === 0 && !Object.keys(f.cf).length ? (
        <div className="card empty"><p className="muted">No {viewInfo.label.toLowerCase()} match these filters.
          {f.range !== "any" && <> Try a wider poll time, or <button className="ghost small-btn" onClick={() => set({ range: "any" })}>any time</button>.</>}</p></div>
      ) : (
        <>
          <Sheet columns={page.columns} rows={page.rows} offset={offset} storageKey={f.view === "json" || f.view === "wide" ? `${f.view}:${f.category}` : f.view}
            sort={sort} onSort={sortBy} loading={loading}
            filters={f.cf} onFilter={(k, v) => { const cf = { ...f.cf }; if (v) cf[k] = v; else delete cf[k]; set({ cf }); }}
            display={(c, v) => (c.key === "plant" && typeof v === "string" ? v.toUpperCase() : cellText(v, c.type))}
            cellLink={(r, c) => {
              const m = /^p(\d+)$/.exec(c.key);
              const id = m ? m[1] : r.poll_id;
              return id ? { href: `#/attempt/${id}`, label: `Open poll #${id} (raw JSON)` } : null;
            }}
            footer={
              <div className="pager">
                <span className="muted small">Rows {(offset + 1).toLocaleString()}–{last.toLocaleString()} of {total.toLocaleString()}</span>
                <label className="small muted">Rows per page{" "}
                  <select value={size} onChange={e => { setSize(Number(e.target.value)); setOffset(0); }}>
                    {PAGE_SIZES.map(n => <option key={n} value={n}>{n}</option>)}
                  </select>
                </label>
                <button className="ghost small-btn" disabled={offset === 0} onClick={() => setOffset(0)}>First</button>
                <button className="ghost small-btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - size))}>Previous</button>
                <button className="ghost small-btn" disabled={last >= total} onClick={() => setOffset(offset + size)}>Next</button>
                <button className="ghost small-btn" disabled={last >= total} onClick={() => setOffset(Math.floor((total - 1) / size) * size)}>Last</button>
              </div>
            } />
        </>
      ))}
      </>}
    </>
  );
}
