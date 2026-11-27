import { useEffect, useState } from "react";
import { api, CAT_LABEL, fmt } from "../api";
import { Skeleton, Spinner } from "../Icon";

export default function Alerts({ canAck }: { canAck: boolean }) {
  const [openOnly, setOpenOnly] = useState(true);
  const [rows, setRows] = useState<any[] | null>(null);
  const [acking, setAcking] = useState<number | null>(null);
  const [err, setErr] = useState("");
  const load = () => api(`/alerts?open=${openOnly}`).then(r => { setRows(r); setErr(""); }).catch(e => setErr(e.message));
  useEffect(() => { load(); }, [openOnly]);
  const ack = async (id: number) => {
    setAcking(id); setErr("");
    try { await api(`/alerts/${id}/ack`, { method: "POST" }); await load(); }
    catch (e: any) { setErr(`Couldn't acknowledge: ${e.message}`); }
    setAcking(null);
  };
  return (
    <>
      <div className="title-row">
        <h2>Alert log</h2>
        {rows && <span className="badge">{rows.length} {openOnly ? "open" : "total"}</span>}
        <label className="toggle"><input type="checkbox" checked={openOnly} onChange={e => setOpenOnly(e.target.checked)} /> Open only</label>
      </div>
      <p className="muted small">Alerts are raised automatically from major compliance flags, failed calls and missed deadlines, and cleared when a later poll is clean. They are kept as an audit log and never deleted.</p>
      {err && <div className="error" role="alert">{err}</div>}
      {!rows ? (err ? null : <Skeleton rows={5} />) : !rows.length ? <div className="card okline">No {openOnly ? "open " : ""}alerts. Every polled endpoint is clean.</div> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Raised</th><th>Plant</th><th>Endpoint</th><th>Rule</th><th>Message</th><th className="num">Seen</th><th>Last seen</th><th>Cleared</th><th></th></tr></thead>
          <tbody>{rows.map(a => (
            <tr key={a.id}>
              <td className="nowrap">{fmt(a.raised_at)}</td><td><a href={`#/plant/${a.plant}`} className="mnem">{a.plant.toUpperCase()}</a></td>
              <td>{CAT_LABEL[a.category] || a.category}</td><td><code>{a.rule}</code></td><td className="msg-cell">{a.message}</td>
              <td className="num">{a.occurrences}×</td><td>{fmt(a.last_seen_at)}</td>
              <td>{a.cleared_at ? fmt(a.cleared_at) : <span className="badge red">open</span>}</td>
              <td>{a.acknowledged_at ? <span className="muted small">ack {fmt(a.acknowledged_at).slice(5)}</span>
                : canAck && !a.cleared_at ? <button className="ghost small-btn" disabled={acking === a.id} onClick={() => ack(a.id)}>{acking === a.id ? <><Spinner />Saving…</> : "Acknowledge"}</button> : null}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}
