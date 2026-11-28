import { useEffect, useState } from "react";
import { api, download } from "../api";
import Icon, { Skeleton, Spinner } from "../Icon";
import Sheet, { SheetCol } from "../Sheet";
import { Pager } from "../components";

/* The client's report templates, shown online as they will look in Excel and downloaded as .xlsx.
   Forecast APIs use the DDQ layout (one column per LSS); MMF uses one sheet per parameter (rows are
   LSS + MMF station, HR1..HRn are 15-minute intervals) plus an Anomaly Report sheet. */

type TSheet = {
  name: string; title: string; info_headers: string[]; info_values: string[]; generated: string; records: number;
  freeze_cols: number; note: string | null; columns: SheetCol[]; rows: Record<string, any>[];
  replaced: [number, number][]; replaced_total: number;
};
type TPage = { category: string; sheets: { name: string; records: number }[]; sheet: TSheet; total: number };

const IV_KEY = "gso_tpl_opts";
function loadOpts(): { fc: number; mmf: number; source: string } {
  try { return { fc: 15, mmf: 15, source: "station", ...JSON.parse(localStorage.getItem(IV_KEY) || "{}") }; }
  catch { return { fc: 15, mmf: 15, source: "station" }; }
}

export default function ReportTemplate({ query: baseQuery, category, fileHint }: { query: string; category: string; fileHint: string }) {
  const isMmf = category === "mmf";
  const [opts, setOpts] = useState(loadOpts);
  const setOpt = (patch: Partial<typeof opts>) => setOpts(o => {
    const n = { ...o, ...patch };
    try { localStorage.setItem(IV_KEY, JSON.stringify(n)); } catch { /* ignore */ }
    return n;
  });
  const query = `${baseQuery}&interval=${isMmf ? opts.mmf : opts.fc}${isMmf ? `&source=${opts.source}` : ""}`;
  const [sheet, setSheet] = useState(0);
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(200);
  const [d, setD] = useState<TPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  // New filters: back to the first sheet and page.
  useEffect(() => { setSheet(0); setOffset(0); }, [query]);
  useEffect(() => {
    let live = true;
    setLoading(true); setErr("");
    const t = setTimeout(() => {
      api<TPage>(`/reports/template?${query}&sheet=${sheet}&offset=${offset}&limit=${limit}`)
        .then(x => { if (live) setD(x); })
        .catch(e => { if (live) { setErr(e.message); setD(null); } })
        .finally(() => { if (live) setLoading(false); });
    }, 200);
    return () => { live = false; clearTimeout(t); };
  }, [query, sheet, offset, limit]);

  const xlsx = async () => {
    setBusy(true); setErr("");
    try { await download(`/reports/template.xlsx?${query}`, fileHint); } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };

  const sh = d?.sheet;
  return (
    <>
      <div className="report-bar tpl-opts">
        <label className="field"><span>Interval</span>
          <select value={isMmf ? opts.mmf : opts.fc} onChange={e => setOpt(isMmf ? { mmf: Number(e.target.value) } : { fc: Number(e.target.value) })}>
            {(isMmf ? [5, 15, 60] : [15, 60]).map(n => <option key={n} value={n}>{n === 60 ? "1 hour" : `${n} minutes`}</option>)}
          </select>
        </label>
        {isMmf && (
          <div className="field"><span>MMF rows</span>
            <div className="tabs seg" role="radiogroup" aria-label="MMF rows">
              {[["station", "Individual MMF"], ["average", "Average of MMFs"]].map(([k, l]) => (
                <button key={k} role="radio" aria-checked={opts.source === k} className={opts.source === k ? "on" : ""} onClick={() => setOpt({ source: k })}>{l}</button>
              ))}
            </div>
          </div>
        )}
        <span className="muted small report-count" role="status">
          {loading ? <><Spinner /> Loading…</> : sh ? <>{d!.sheets.length > 1 ? `${d!.sheets.length} sheets · ` : ""}{sh.records.toLocaleString()} record(s) on this sheet</> : null}
        </span>
        <button className="push-right" onClick={xlsx} disabled={busy || !d}>{busy ? <><Spinner />Preparing Excel…</> : <><Icon name="download" />Download Excel (.xlsx)</>}</button>
      </div>
      {err && <div className="error" role="alert">{err}</div>}
      {!d && loading && <Skeleton rows={8} />}
      {sh && (
        <div className="tpl">
          <div className="tpl-title">{sh.title}</div>
          <div className="tpl-info-wrap">
            <table className="tpl-info">
              <thead><tr>{sh.info_headers.map(h => <th key={h} scope="col">{h}</th>)}</tr></thead>
              <tbody><tr>{sh.info_values.map((v, i) => <td key={i}>{v}</td>)}</tr></tbody>
            </table>
          </div>
          <div className="tpl-gen"><span>{sh.generated}</span><span>{sh.records.toLocaleString()} record(s)</span>
            {sh.replaced_total > 0 && <span className="tpl-key"><i className="tpl-swatch" aria-hidden="true" />
              {sh.replaced_total.toLocaleString()} cell(s) had no reading and show 0 (see Anomaly Report)</span>}
            {sh.note && <b className="bad">{sh.note}</b>}</div>
          {sh.records === 0 ? (
            <div className="card empty"><p className="muted">No submissions match these filters. Try a wider retrieval time or more plants.</p></div>
          ) : (
            <Sheet columns={sh.columns} rows={sh.rows} offset={offset} storageKey={`tpl:${d!.category}:${sh.name}`}
              loading={loading} frozen={sh.freeze_cols}
              cellClass={(() => { const set = new Set(sh.replaced.map(([r, c]) => `${r}:${c}`)); return (r: number, c: number) => (set.has(`${r}:${c}`) ? "repl" : ""); })()}
              footer={<Pager total={sh.records} offset={offset} limit={limit} onOffset={setOffset}
                onLimit={n => { setLimit(n); setOffset(0); }} sizes={[100, 200, 500, 1000]} />} />
          )}
          {d!.sheets.length > 1 && (
            <div className="tpl-tabs" role="tablist" aria-label="Sheets">
              {d!.sheets.map((x, i) => (
                <button key={x.name} role="tab" aria-selected={i === sheet} className={i === sheet ? "on" : ""}
                  onClick={() => { setSheet(i); setOffset(0); }} title={`${x.records.toLocaleString()} record(s)`}>{x.name}</button>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}
