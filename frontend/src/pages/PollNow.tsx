import { Fragment, useEffect, useRef, useState } from "react";
import { api, download, CATS, CAT_LABEL, fmt } from "../api";
import Inspect from "./Inspect";
import Icon, { Skeleton, Spinner } from "../Icon";

type Alert = { id: number; rule: string; severity: string; message: string; raised_at: string; occurrences: number };
type Result = {
  plant: string; category: string; url: string; polled: boolean; off_reason: string | null;
  verdict: "pass" | "warn" | "fail"; reason: string; http_status: number | null; latency_ms: number | null;
  bytes: number | null; attempt_id: number | null; tested_at: string; major: string[]; minor: string[];
  alerts: { raised: Alert[]; ongoing: Alert[]; cleared: Alert[] };
};
type Summary = { pass: number; warn: number; fail: number; alerts_raised: number; alerts_ongoing: number; alerts_cleared: number };
const VERDICT = { pass: "Pass", warn: "Pass with warnings", fail: "Fail" };

/** Link to this page with a scope, optionally running it straight away. */
export const pollHref = (plant = "all", category = "all", run = true) =>
  `#/admin/poll/${plant}/${category}${run ? "/run" : ""}`;

// Change the address without a hashchange, so the page isn't remounted.
const history_replace = (href: string) => window.history.replaceState(null, "", href);

type Run = { id: number; polled_at: string; finished_at: string | null; by: string | null; plants: string[] | null;
  category: string | null; summary: Summary; count: number; results?: Result[] };

const scopeLabel = (plants: string[] | null, c: string | null) =>
  `${!plants ? "All plants" : plants.map(x => x.toUpperCase()).join(", ")} · ${!c ? "all APIs" : CAT_LABEL[c]}`;

export default function PollNow({ plant: p0 = "all", category: c0 = "all", autorun = false, runId }:
  { plant?: string; category?: string; autorun?: boolean; runId?: string }) {
  const [plants, setPlants] = useState<{ mnemonic: string; name: string }[]>([]);
  const [plant, setPlant] = useState(p0);
  const [category, setCategory] = useState(c0);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState("");
  const [out, setOut] = useState<(Run & { results: Result[] }) | null>(null);
  const [history, setHistory] = useState<Run[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const started = useRef(false);

  const loadHistory = () => api<Run[]>("/admin/poll-runs?limit=20").then(h => { setHistory(h); return h; });

  // Show a run: problems first, and open the raw JSON of a single result or the first failure.
  const show = (r: Run & { results: Result[] }, openFirst = true) => {
    const rank = { fail: 0, warn: 1, pass: 2 } as Record<string, number>;
    const results = [...r.results].sort((x, y) => rank[x.verdict] - rank[y.verdict]);
    setOut({ ...r, results });
    setPlant(r.plants?.length === 1 ? r.plants[0] : "all");
    setCategory(r.category || "all");
    const first = results.length === 1 ? results[0] : results.find(x => x.verdict === "fail");
    setOpen(openFirst && first?.attempt_id ? first.attempt_id : null);
  };
  const goTo = (id: number) => { try { history_replace(`#/admin/poll/runs/${id}`); } catch { /* ignore */ } };

  const run = async (pl = plant, c = category) => {
    setRunning(true); setErr(""); setOpen(null);
    try {
      const r = await api("/admin/poll-now", { method: "POST", body: {
        plants: pl === "all" ? null : [pl], category: c === "all" ? null : c } });
      show(r);
      goTo(r.id);  // the run now has its own address, and a reload won't poll again
      loadHistory().catch(() => {});
    } catch (e: any) { setErr(e.message); }
    setRunning(false);
  };

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    api("/plants").then(setPlants).catch(e => setErr(e.message));
    if (autorun) { run(p0, c0); return; }
    if (runId) {
      api(`/admin/poll-runs/${runId}`).then(r => show(r)).catch(e => setErr(e.message));
      loadHistory().catch(() => {});
      return;
    }
    // Coming back to the page: reopen the latest run.
    loadHistory().then(h => {
      if (!h.length) return;
      return api(`/admin/poll-runs/${h[0].id}`).then(r => { show(r, false); goTo(r.id); });
    }).catch(e => setErr(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const s = out?.summary;
  return (
    <>
      <div className="title-row"><h2>Poll now</h2></div>
      <p className="muted small">Calls the chosen APIs immediately with each plant's stored key, then shows the raw JSON each one returned,
        with the parts that broke a rule highlighted, and the alerts it raised or cleared. Unlike <b>Test</b> on the Plants page,
        this is meant for looking at the data: it runs even when scheduled polling is paused, and each call is stored, validated and
        alerted on like a scheduled poll, marked as manual.</p>

      <div className="card poll-form">
        <label className="field"><span>Plant</span>
          <select value={plant} onChange={e => setPlant(e.target.value)} disabled={running}>
            <option value="all">All plants (whole fleet)</option>
            {plants.map(x => <option key={x.mnemonic} value={x.mnemonic}>{x.mnemonic.toUpperCase()}{x.name && x.name.toLowerCase() !== x.mnemonic ? ` · ${x.name}` : ""}</option>)}
          </select>
        </label>
        <label className="field"><span>API</span>
          <select value={category} onChange={e => setCategory(e.target.value)} disabled={running}>
            <option value="all">All APIs</option>
            {CATS.map(c => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}
          </select>
        </label>
        <button onClick={() => run()} disabled={running}>{running ? <><Spinner />Polling…</> : <><Icon name="zap" />Poll now</>}</button>
      </div>
      {err && <div className="error" role="alert">{err}</div>}
      {running && !out && <Skeleton rows={4} />}

      {out && s && (
        <>
          <div className="poll-summary" role="status">
            <b>{scopeLabel(out.plants, out.category)}</b><span className="muted">polled {fmt(out.polled_at)}{out.by ? ` by ${out.by}` : ""}</span>
            <span><span className="verdict pass">{s.pass} pass</span> <span className="verdict warn">{s.warn} warnings</span> <span className="verdict fail">{s.fail} fail</span></span>
            <span>Alerts: <b className={s.alerts_raised ? "txt-red" : ""}>{s.alerts_raised} new</b> · {s.alerts_ongoing} still open · <b className={s.alerts_cleared ? "txt-green" : ""}>{s.alerts_cleared} cleared</b></span>
            {out.id && <span className="actions">
              <a className="button ghost small-btn" href={`#/reports?run=${out.id}&range=any`}><Icon name="table" />View data as table</a>
              <button className="ghost small-btn" onClick={() => download(`/reports/export.csv?run=${out.id}&view=json`).catch(e => setErr(e.message))}><Icon name="download" />Download CSV</button>
            </span>}
          </div>
          <div className="table-wrap">
            <table className="poll-results">
              <thead><tr><th>Plant</th><th>API</th><th>Result</th><th>Alerts</th><th></th></tr></thead>
              <tbody>
                {out.results.map(r => {
                  const isOpen = open !== null && open === r.attempt_id;
                  const al = r.alerts;
                  return (
                    <Fragment key={`${r.plant}:${r.category}`}>
                      <tr className={isOpen ? "sel" : ""}>
                        <td><a href={`#/plant/${r.plant}`} className="mnem">{r.plant.toUpperCase()}</a></td>
                        <td>{CAT_LABEL[r.category]}{!r.polled && <div className="small muted" title={r.off_reason || ""}>paused on schedule</div>}</td>
                        <td>
                          <div className="verdict-wrap">
                            <span className={`verdict ${r.verdict}`}>{VERDICT[r.verdict]}</span>
                            <span className="small muted">{r.http_status ? `HTTP ${r.http_status}` : "no response"} · {r.latency_ms ?? "—"} ms
                              {r.bytes ? ` · ${r.bytes >= 1024 ? Math.round(r.bytes / 1024) + " KB" : r.bytes + " B"}` : ""}</span>
                            <span className="small">{r.reason}</span>
                            {r.major.length + r.minor.length > 1 && <span className="small muted">{r.major.length} major, {r.minor.length} minor findings</span>}
                          </div>
                        </td>
                        <td className="alert-cell">
                          {!al.raised.length && !al.ongoing.length && !al.cleared.length && <span className="muted small">None</span>}
                          {al.raised.map(a => <div key={a.id} className="alert-chip new" title={a.message}><b>New</b> {a.rule}<span>{a.message}</span></div>)}
                          {al.ongoing.map(a => <div key={a.id} className="alert-chip open" title={`Open since ${fmt(a.raised_at)}, seen ${a.occurrences} times`}><b>Still open</b> {a.rule}<span>{a.message} · since {fmt(a.raised_at)}</span></div>)}
                          {al.cleared.map(a => <div key={a.id} className="alert-chip cleared" title={a.message}><b>Cleared</b> {a.rule}<span>{a.message}</span></div>)}
                        </td>
                        <td>{r.attempt_id && <button className="ghost small-btn" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : r.attempt_id)}><Icon name="chevron" size={14} className="chev" />{isOpen ? "Hide raw JSON" : "Raw JSON"}</button>}</td>
                      </tr>
                      {isOpen && (
                        <tr className="inspect-row"><td colSpan={5}><Inspect key={r.attempt_id} id={String(r.attempt_id)} embedded /></td></tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          {out.summary.alerts_raised + out.summary.alerts_ongoing > 0 && <p className="small muted">All open alerts are on the <a href="#/alerts">Alerts</a> page.</p>}
          <p className="small muted">Alerts are shown as they stood right after this run. Raw JSON is the response stored for each call.</p>
        </>
      )}

      <div className="card poll-history">
        <div className="findings-head"><b>Recent runs</b><span className="muted small">Each run has its own link, so you can come back to it or share it.</span></div>
        {history === null ? <Skeleton rows={3} /> : !history.length ? <p className="muted small">No runs yet.</p> : (
          <table>
            <thead><tr><th>When</th><th>Scope</th><th>Result</th><th>Alerts</th><th>By</th></tr></thead>
            <tbody>
              {history.map(h => (
                <tr key={h.id} className={out?.id === h.id ? "sel" : ""}>
                  <td><a href={`#/admin/poll/runs/${h.id}`}>{fmt(h.polled_at)}</a></td>
                  <td>{scopeLabel(h.plants, h.category)} <span className="muted small">({h.count} call{h.count === 1 ? "" : "s"})</span></td>
                  <td className="nowrap">{h.summary.fail ? <span className="verdict fail">{h.summary.fail} fail</span> : null} {h.summary.warn ? <span className="verdict warn">{h.summary.warn} warn</span> : null} {h.summary.pass ? <span className="verdict pass">{h.summary.pass} pass</span> : null}</td>
                  <td className="small">{h.summary.alerts_raised} new · {h.summary.alerts_ongoing} open · {h.summary.alerts_cleared} cleared</td>
                  <td className="small muted">{h.by || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
