import { Fragment, useEffect, useRef, useState } from "react";
import { api, CATS, CAT_LABEL, fmt } from "../api";
import Inspect from "./Inspect";

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

export default function PollNow({ plant: p0 = "all", category: c0 = "all", autorun = false }:
  { plant?: string; category?: string; autorun?: boolean }) {
  const [plants, setPlants] = useState<{ mnemonic: string; name: string }[]>([]);
  const [plant, setPlant] = useState(p0);
  const [category, setCategory] = useState(c0);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState("");
  const [out, setOut] = useState<{ polled_at: string; results: Result[]; summary: Summary; scope: string } | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const ran = useRef(false);

  useEffect(() => { api("/plants").then(setPlants).catch(e => setErr(e.message)); }, []);

  const scopeLabel = (pl: string, c: string) =>
    `${pl === "all" ? "All plants" : pl.toUpperCase()} · ${c === "all" ? "all APIs" : CAT_LABEL[c]}`;

  const run = async (pl = plant, c = category) => {
    setRunning(true); setErr(""); setOpen(null);
    try {
      const r = await api("/admin/poll-now", { method: "POST", body: {
        plants: pl === "all" ? null : [pl], category: c === "all" ? null : c } });
      // Problems first, so a fleet-wide poll shows what needs attention at the top.
      const rank = { fail: 0, warn: 1, pass: 2 } as Record<string, number>;
      r.results.sort((x: Result, y: Result) => rank[x.verdict] - rank[y.verdict]);
      setOut({ ...r, scope: scopeLabel(pl, c) });
      // One result: show its raw JSON straight away. Several: open the first one that needs attention.
      const first = r.results.length === 1 ? r.results[0] : r.results.find((x: Result) => x.verdict === "fail");
      if (first?.attempt_id) setOpen(first.attempt_id);
    } catch (e: any) { setErr(e.message); }
    setRunning(false);
  };

  useEffect(() => {
    if (autorun && !ran.current) {
      ran.current = true;
      // Drop "/run" from the address so a page reload doesn't poll again.
      try { history.replaceState(null, "", pollHref(p0, c0, false)); } catch { /* ignore */ }
      run(p0, c0);
    }
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
        <button onClick={() => run()} disabled={running}>{running ? "Polling…" : "Poll now"}</button>
      </div>
      {err && <div className="error">{err}</div>}

      {out && s && (
        <>
          <div className="poll-summary">
            <b>{out.scope}</b><span className="muted">polled {fmt(out.polled_at)}</span>
            <span><span className="verdict pass">{s.pass} pass</span> <span className="verdict warn">{s.warn} warnings</span> <span className="verdict fail">{s.fail} fail</span></span>
            <span>Alerts: <b className={s.alerts_raised ? "txt-red" : ""}>{s.alerts_raised} new</b> · {s.alerts_ongoing} still open · <b className={s.alerts_cleared ? "txt-green" : ""}>{s.alerts_cleared} cleared</b></span>
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
                        <td><a href={`#/plant/${r.plant}`}><b>{r.plant.toUpperCase()}</b></a></td>
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
                        <td>{r.attempt_id && <button className="ghost small-btn" onClick={() => setOpen(isOpen ? null : r.attempt_id)}>{isOpen ? "Hide raw JSON" : "Raw JSON"}</button>}</td>
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
        </>
      )}
    </>
  );
}
