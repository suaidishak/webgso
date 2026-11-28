import { Fragment, useEffect, useRef, useState } from "react";
import { api, download, CATS, CAT_LABEL, fmt } from "../api";
import Inspect from "./Inspect";
import Icon, { Skeleton, Spinner } from "../Icon";
import { PlantPicker } from "../components";

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
/** Reports for one Retrieve now run, in the client template, as if it were a scheduled submission. */
const runReport = (r: { id: number; category: string | null }) =>
  `#/reports?run=${r.id}&range=any&fmt=template${r.category && r.category !== "all" ? `&category=${r.category}` : ""}`;

export const pollHref = (plant = "all", category = "all", run = true) =>
  `#/admin/poll/${plant}/${category}${run ? "/run" : ""}`;

// Change the address without a hashchange, so the page isn't remounted.
const history_replace = (href: string) => window.history.replaceState(null, "", href);

type Run = { id: number; polled_at: string; finished_at: string | null; by: string | null; plants: string[] | null;
  category: string | null; summary: Summary; count: number; status: string; done: number; total: number; results?: Result[] };

const scopeLabel = (plants: string[] | null, c: string | null) =>
  `${!plants ? "All plants" : plants.length > 6 ? `${plants.length} plants` : plants.map(x => x.toUpperCase()).join(", ")} · ${!c ? "all APIs" : CAT_LABEL[c]}`;
const RESULTS_PAGE = 100;

export default function PollNow({ plant: p0 = "all", category: c0 = "all", autorun = false, runId }:
  { plant?: string; category?: string; autorun?: boolean; runId?: string }) {
  const initial = p0 === "all" ? [] : p0.split(",").filter(Boolean);
  const [plants, setPlants] = useState<string[]>(initial);
  const [category, setCategory] = useState(c0);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState("");
  const [out, setOut] = useState<(Run & { results: Result[] }) | null>(null);
  const [progress, setProgress] = useState<Run | null>(null);
  const [history, setHistory] = useState<Run[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [verdict, setVerdict] = useState("");
  const [find, setFind] = useState("");
  const [shownN, setShownN] = useState(RESULTS_PAGE);
  const started = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const loadHistory = () => api<Run[]>("/admin/poll-runs?limit=20").then(h => { setHistory(h); return h; });

  // Show a run: problems first, and open the raw JSON of a single result or the first failure.
  const show = (r: Run & { results: Result[] }, openFirst = true) => {
    const rank = { fail: 0, warn: 1, pass: 2 } as Record<string, number>;
    const results = [...r.results].sort((x, y) => rank[x.verdict] - rank[y.verdict]);
    setOut({ ...r, results });
    setProgress(null);
    setPlants(r.plants || []);
    setCategory(r.category || "all");
    setVerdict(""); setFind(""); setShownN(RESULTS_PAGE);
    const first = results.length === 1 ? results[0] : results.find(x => x.verdict === "fail");
    setOpen(openFirst && first?.attempt_id ? first.attempt_id : null);
  };
  const goTo = (id: number) => { try { history_replace(`#/admin/poll/runs/${id}`); } catch { /* ignore */ } };

  // A large run (hundreds of plants) carries on in the background: follow it until it finishes.
  const follow = async (r: Run & { results: Result[] }, openFirst = true) => {
    let cur = r;
    while (cur.status === "running" && alive.current) {
      setProgress(cur);
      await new Promise(res => setTimeout(res, 1500));
      cur = await api(`/admin/poll-runs/${cur.id}`);
    }
    if (!alive.current) return;
    if (cur.status === "failed") { setProgress(null); setErr(`The run stopped: ${(cur.summary as any)?.error || "unknown error"}`); return; }
    show(cur, openFirst);
  };

  const run = async (pl = plants, c = category) => {
    setRunning(true); setErr(""); setOpen(null); setOut(null);
    try {
      const r = await api("/admin/poll-now", { method: "POST", body: {
        plants: pl.length ? pl : null, category: c === "all" ? null : c } });
      goTo(r.id);  // the run now has its own address, and a reload won't poll again
      await follow(r);
      loadHistory().catch(() => {});
    } catch (e: any) { setErr(e.message); setProgress(null); }
    setRunning(false);
  };

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (autorun) { loadHistory().catch(() => {}); run(initial, c0); return; }
    if (runId) {
      api(`/admin/poll-runs/${runId}`).then(r => { setPlants(r.plants || []); setCategory(r.category || "all"); return follow(r); }).catch(e => setErr(e.message));
      loadHistory().catch(() => {});
      return;
    }
    // Coming back to the page: reopen the latest run.
    loadHistory().then(h => {
      if (!h.length) return;
      return api(`/admin/poll-runs/${h[0].id}`).then(r => { goTo(r.id); return follow(r, false); });
    }).catch(e => setErr(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const results = (out?.results || []).filter(r => (!verdict || r.verdict === verdict)
    && (!find || r.plant.includes(find.trim().toLowerCase())));
  const s = out?.summary;
  return (
    <>
      <div className="title-row"><h2>Retrieve now</h2></div>
      <p className="muted small">Calls the chosen APIs immediately with each plant's stored key, then shows the raw JSON each one returned,
        with the parts that broke a rule highlighted, and the alerts it raised or cleared. Unlike <b>Test</b> on the Plants page,
        this is meant for looking at the data: it runs even when scheduled retrieval is paused, and each call is stored, validated and
        alerted on like a scheduled retrieval, marked as manual.</p>

      <div className="card poll-form">
        <div className="field"><span>Plants</span>
          <PlantPicker value={plants} onChange={setPlants} disabled={running} allLabel="All plants (whole fleet)" />
        </div>
        <label className="field"><span>API</span>
          <select value={category} onChange={e => setCategory(e.target.value)} disabled={running}>
            <option value="all">All APIs</option>
            {CATS.map(c => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}
          </select>
        </label>
        <button onClick={() => run()} disabled={running}>{running ? <><Spinner />Retrieving…</> : <><Icon name="zap" />Retrieve now</>}</button>
      </div>
      {err && <div className="error" role="alert">{err}</div>}
      {progress && (
        <div className="card run-progress" role="status">
          <div className="findings-head"><b><Spinner /> Retrieving {scopeLabel(progress.plants, progress.category)}</b>
            <span className="muted small">{progress.done} of {progress.total} calls done · started {fmt(progress.polled_at)}{progress.by ? ` by ${progress.by}` : ""}</span></div>
          <div className="progress" aria-hidden="true"><i style={{ width: `${Math.round(100 * progress.done / Math.max(1, progress.total))}%` }} /></div>
          <p className="small muted">Large runs carry on in the background. You can leave this page and come back with this run's link; the results appear here when every call is done.</p>
        </div>
      )}
      {running && !out && !progress && <Skeleton rows={4} />}

      {out && s && (
        <>
          <div className="poll-summary" role="status">
            <b>{scopeLabel(out.plants, out.category)}</b><span className="muted">retrieved {fmt(out.polled_at)}{out.by ? ` by ${out.by}` : ""}</span>
            <span><span className="verdict pass">{s.pass} pass</span> <span className="verdict warn">{s.warn} warnings</span> <span className="verdict fail">{s.fail} fail</span></span>
            <span>Alerts: <b className={s.alerts_raised ? "txt-red" : ""}>{s.alerts_raised} new</b> · {s.alerts_ongoing} still open · <b className={s.alerts_cleared ? "txt-green" : ""}>{s.alerts_cleared} cleared</b></span>
            {out.id && <span className="actions">
              <a className="button ghost small-btn" href={runReport(out)}><Icon name="table" />View in Reports</a>
              <a className="button ghost small-btn" href={`#/reports?run=${out.id}&range=any&fmt=table`}><Icon name="list" />View data as table</a>
              <button className="ghost small-btn" onClick={() => download(`/reports/export.csv?run=${out.id}&view=json`).catch(e => setErr(e.message))}><Icon name="download" />Download CSV</button>
            </span>}
          </div>
          {out.results.length > 10 && (
            <div className="board-row result-filter">
              <div className="chips" role="group" aria-label="Show results">
                {([["", "All", out.results.length], ["fail", "Fail", s.fail], ["warn", "Warnings", s.warn], ["pass", "Pass", s.pass]] as [string, string, number][]).map(([k, l, n]) => (
                  <label key={k || "all"} className={`chip ${verdict === k ? "on" : ""}`}><input type="radio" name="verdict" checked={verdict === k} onChange={() => { setVerdict(k); setShownN(RESULTS_PAGE); }} />{l}<span className="chip-n">{n}</span></label>
                ))}
              </div>
              <input className="search" type="search" placeholder="Find plant" aria-label="Find plant in results" value={find} onChange={e => { setFind(e.target.value); setShownN(RESULTS_PAGE); }} />
            </div>
          )}
          <div className="table-wrap">
            <table className="poll-results">
              <thead><tr><th>Plant</th><th>API</th><th>Result</th><th>Alerts</th><th></th></tr></thead>
              <tbody>
                {!results.length && <tr><td colSpan={5} className="muted">No results match.</td></tr>}
                {results.slice(0, shownN).map(r => {
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
          {results.length > shownN && (
            <div className="pager"><span className="muted small">Showing {shownN} of {results.length} results, problems first</span>
              <button className="ghost small-btn" onClick={() => setShownN(n => n + RESULTS_PAGE * 2)}>Show more</button>
              <button className="ghost small-btn" onClick={() => setShownN(results.length)}>Show all</button></div>
          )}
          {out.summary.alerts_raised + out.summary.alerts_ongoing > 0 && <p className="small muted">All open alerts are on the <a href="#/alerts">Alerts</a> page.</p>}
          <p className="small muted">Alerts are shown as they stood right after this run. Raw JSON is the response stored for each call.</p>
        </>
      )}

      <div className="card poll-history">
        <div className="findings-head"><b>Recent runs</b><span className="muted small">Click a time to see that run's data in Reports, in the client template like a scheduled submission. Run details shows the calls, raw JSON and alerts.</span></div>
        {history === null ? <Skeleton rows={3} /> : !history.length ? <p className="muted small">No runs yet.</p> : (
          <table>
            <thead><tr><th>When</th><th>Scope</th><th>Result</th><th>Alerts</th><th>By</th><th><span className="sr-only">Run details</span></th></tr></thead>
            <tbody>
              {history.map(h => (
                <tr key={h.id} className={out?.id === h.id ? "sel" : ""}>
                  <td><a href={runReport(h)} title="Open this run's data in Reports, laid out like a scheduled submission">{fmt(h.polled_at)}</a></td>
                  <td>{scopeLabel(h.plants, h.category)} <span className="muted small">({h.count} call{h.count === 1 ? "" : "s"})</span></td>
                  <td className="nowrap">{h.status === "running" ? <span className="verdict running"><Spinner />{h.done}/{h.total}</span> : h.status === "failed" ? <span className="verdict fail">stopped</span> : null} {h.summary.fail ? <span className="verdict fail">{h.summary.fail} fail</span> : null} {h.summary.warn ? <span className="verdict warn">{h.summary.warn} warn</span> : null} {h.summary.pass ? <span className="verdict pass">{h.summary.pass} pass</span> : null}</td>
                  <td className="small">{h.summary.alerts_raised} new · {h.summary.alerts_ongoing} open · {h.summary.alerts_cleared} cleared</td>
                  <td className="small muted">{h.by || "—"}</td>
                  <td><a className="small nowrap" href={`#/admin/poll/runs/${h.id}`}>Run details</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
