import { useEffect, useState } from "react";
import { api, CAT_LABEL, fmt } from "../api";
import { Skeleton } from "../Icon";

export default function Compliance() {
  const [summary, setSummary] = useState<any[] | null>(null);
  const [deadlines, setDeadlines] = useState<any[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api("/compliance/summary").then(setSummary).catch(e => setErr(e.message));
    api("/compliance/deadlines?limit=100").then(setDeadlines).catch(e => setErr(e.message));
  }, []);
  const counts = (deadlines || []).reduce((m: Record<string, number>, d) => ({ ...m, [d.status]: (m[d.status] || 0) + 1 }), {} as Record<string, number>);
  return (
    <>
      <h2>Compliance</h2>
      {err && <div className="error" role="alert">{err}</div>}
      <h3>Rule breaks in the last 24 hours</h3>
      {!summary ? <Skeleton rows={3} /> : !summary.length ? <div className="card okline">No flags in the last 24 hours.</div> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Plant</th><th>Endpoint</th><th>Severity</th><th>Rule</th><th className="num">Times</th><th>Example</th></tr></thead>
          <tbody>{summary.map((r, i) => (
            <tr key={i}><td><a href={`#/plant/${r.mnemonic}`} className="mnem">{r.mnemonic.toUpperCase()}</a></td><td>{CAT_LABEL[r.category]}</td>
              <td><span className={`sev ${r.severity}`}>{r.severity}</span></td><td><code>{r.rule}</code></td>
              <td className="num">{r.n}</td><td className="small">{r.example}</td></tr>
          ))}</tbody>
        </table></div>
      )}
      <div className="title-row" style={{ marginTop: 28 }}>
        <h3 style={{ margin: 0 }}>Publish deadlines</h3>
        {deadlines && deadlines.length > 0 && <span className="actions">
          <span className="badge green">{counts.met || 0} met</span><span className="badge amber">{counts.late || 0} late</span><span className="badge red">{counts.missed || 0} missed</span>
        </span>}
      </div>
      <p className="muted small">Checked after each deadline plus its grace period: met, late (arrived after) or missed.</p>
      {!deadlines ? <Skeleton rows={4} /> : !deadlines.length ? <p className="muted">No deadlines have passed since polling started.</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Due</th><th>Plant</th><th>Endpoint</th><th>Covers from</th><th>First valid</th><th>Status</th></tr></thead>
          <tbody>{deadlines.map((d, i) => (
            <tr key={i}><td>{fmt(d.due_at)}</td><td><a href={`#/plant/${d.plant}`} className="mnem">{d.plant.toUpperCase()}</a></td><td>{CAT_LABEL[d.category]}</td>
              <td>{fmt(d.target_start).slice(0, 10)}</td><td>{fmt(d.first_valid_at)}</td>
              <td><span className={`badge ${d.status === "met" ? "green" : d.status === "late" ? "amber" : "red"}`}>{d.status}</span></td></tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}
