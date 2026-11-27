import { useEffect, useState } from "react";
import { api, CAT_LABEL, fmt } from "../api";

export default function Compliance() {
  const [summary, setSummary] = useState<any[] | null>(null);
  const [deadlines, setDeadlines] = useState<any[] | null>(null);
  useEffect(() => { api("/compliance/summary").then(setSummary); api("/compliance/deadlines?limit=100").then(setDeadlines); }, []);
  return (
    <>
      <h2>Compliance</h2>
      <h3>Rule breaks in the last 24 hours</h3>
      {!summary ? <p className="muted">Loading…</p> : !summary.length ? <p className="okline">No flags in the last 24 hours.</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Plant</th><th>Endpoint</th><th>Severity</th><th>Rule</th><th className="num">Times</th><th>Example</th></tr></thead>
          <tbody>{summary.map((r, i) => (
            <tr key={i}><td><a href={`#/plant/${r.mnemonic}`}>{r.mnemonic.toUpperCase()}</a></td><td>{CAT_LABEL[r.category]}</td>
              <td><span className={`sev ${r.severity}`}>{r.severity}</span></td><td><code>{r.rule}</code></td>
              <td className="num">{r.n}</td><td className="small">{r.example}</td></tr>
          ))}</tbody>
        </table></div>
      )}
      <h3>Publish deadlines</h3>
      <p className="muted small">Checked after each deadline plus its grace period: met, late (arrived after) or missed.</p>
      {!deadlines ? <p className="muted">Loading…</p> : !deadlines.length ? <p className="muted">No deadlines have passed since polling started.</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Due</th><th>Plant</th><th>Endpoint</th><th>Covers from</th><th>First valid</th><th>Status</th></tr></thead>
          <tbody>{deadlines.map((d, i) => (
            <tr key={i}><td>{fmt(d.due_at)}</td><td>{d.plant.toUpperCase()}</td><td>{CAT_LABEL[d.category]}</td>
              <td>{fmt(d.target_start).slice(0, 10)}</td><td>{fmt(d.first_valid_at)}</td>
              <td><span className={`badge ${d.status === "met" ? "green" : d.status === "late" ? "amber" : "red"}`}>{d.status}</span></td></tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}
