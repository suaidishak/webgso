import { useEffect, useState } from "react";
import { api, CAT_LABEL, fmt } from "../api";

export default function Alerts({ canAck }: { canAck: boolean }) {
  const [openOnly, setOpenOnly] = useState(true);
  const [rows, setRows] = useState<any[] | null>(null);
  const load = () => api(`/alerts?open=${openOnly}`).then(setRows);
  useEffect(() => { load(); }, [openOnly]);
  const ack = async (id: number) => { await api(`/alerts/${id}/ack`, { method: "POST" }); load(); };
  return (
    <>
      <div className="title-row">
        <h2>Alert log</h2>
        <label className="toggle"><input type="checkbox" checked={openOnly} onChange={e => setOpenOnly(e.target.checked)} /> Open only</label>
      </div>
      <p className="muted small">Alerts are raised automatically from major compliance flags, failed calls and missed deadlines, and cleared when a later poll is clean. They are kept as an audit log and never deleted.</p>
      {!rows ? <p className="muted">Loading…</p> : !rows.length ? <p className="okline">No {openOnly ? "open " : ""}alerts.</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Raised</th><th>Plant</th><th>Endpoint</th><th>Rule</th><th>Message</th><th className="num">Seen</th><th>Last seen</th><th>Cleared</th><th></th></tr></thead>
          <tbody>{rows.map(a => (
            <tr key={a.id}>
              <td>{fmt(a.raised_at)}</td><td><a href={`#/plant/${a.plant}`}>{a.plant.toUpperCase()}</a></td>
              <td>{CAT_LABEL[a.category] || a.category}</td><td><code>{a.rule}</code></td><td>{a.message}</td>
              <td className="num">{a.occurrences}×</td><td>{fmt(a.last_seen_at)}</td>
              <td>{a.cleared_at ? fmt(a.cleared_at) : <span className="badge red">open</span>}</td>
              <td>{a.acknowledged_at ? <span className="muted small">ack {fmt(a.acknowledged_at).slice(5)}</span>
                : canAck && !a.cleared_at ? <button className="small-btn" onClick={() => ack(a.id)}>Acknowledge</button> : null}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}
