import { useEffect, useState } from "react";
import { api, CATS, CAT_LABEL, fmt } from "../api";

type Cell = { state: string; attempt_id: number | null; off_reason: string | null; last_poll: string | null; http_status: number | null; latency_ms: number | null;
  major_flags: number; minor_flags: number; error: string | null; last_valid: string | null; open_alerts: number };
type Row = { mnemonic: string; name: string; status: string; cells: Record<string, Cell>; open_alerts: number };

export const STATE_LABEL: Record<string, string> = {
  ok: "Compliant", minor: "Minor issues", invalid: "Non-compliant", down: "Unreachable", never: "Not polled yet", disabled: "Paused",
};

export function Legend() {
  return <div className="legend">{Object.entries(STATE_LABEL).map(([k, v]) => <span key={k}><i className={`dot ${k}`} />{v}</span>)}</div>;
}

export default function Fleet({ isAdmin = false }: { isAdmin?: boolean }) {
  const [data, setData] = useState<{ generated_at: string; plants: Row[]; polling?: { fleet: boolean; types: Record<string, boolean> } } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    const load = () => api("/fleet/status").then(d => alive && setData(d)).catch(e => setErr(e.message));
    load();
    const t = setInterval(load, 30000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  if (err) return <div className="error">{err}</div>;
  if (!data) return <p className="muted">Loading…</p>;
  const counts: Record<string, number> = {};
  data.plants.forEach(p => Object.values(p.cells).forEach(c => (counts[c.state] = (counts[c.state] || 0) + 1)));
  return (
    <>
      <div className="title-row">
        <h2>Fleet compliance</h2>
        <span className="muted small">Updated {fmt(data.generated_at)} MYT · refreshes every 30 s</span>
        {isAdmin && <a className="button ghost push-right" href="#/admin/poll/all/all/run">Poll whole fleet now</a>}
      </div>
      <div className="tiles">
        <div className="tile"><b>{data.plants.length}</b><span>plants</span></div>
        <div className="tile ok"><b>{(counts.ok || 0) + (counts.minor || 0)}</b><span>endpoints valid</span></div>
        <div className="tile invalid"><b>{counts.invalid || 0}</b><span>non-compliant</span></div>
        <div className="tile down"><b>{counts.down || 0}</b><span>unreachable</span></div>
        <div className="tile"><b>{data.plants.reduce((a, p) => a + p.open_alerts, 0)}</b><span>open alerts</span></div>
      </div>
      {data.polling && !data.polling.fleet && <div className="banner">Fleet polling is paused. No plant is being polled on schedule. An admin can resume it on the Plants page.</div>}
      {data.polling && data.polling.fleet && Object.entries(data.polling.types).some(([, v]) => !v) && (
        <div className="banner soft">Paused for all plants: {Object.entries(data.polling.types).filter(([, v]) => !v).map(([k]) => CAT_LABEL[k]).join(", ")}.</div>
      )}
      <Legend />
      <p className="muted small">Click a status to open the plant's latest response with the parts that caused it highlighted.</p>
      <div className="table-wrap">
        <table className="board">
          <thead><tr><th>Plant</th>{CATS.map(c => <th key={c}>{CAT_LABEL[c]}</th>)}<th>Alerts</th></tr></thead>
          <tbody>
            {data.plants.map(p => (
              <tr key={p.mnemonic}>
                <td><a href={`#/plant/${p.mnemonic}`}><b>{p.mnemonic.toUpperCase()}</b></a>
                  {isAdmin && <a className="poll-plant small" href={`#/admin/poll/${p.mnemonic}/all/run`} title={`Poll every ${p.mnemonic.toUpperCase()} API now and show the raw JSON`}>Poll now</a>}</td>
                {CATS.map(c => {
                  const cell = p.cells[c];
                  if (!cell) return <td key={c} className="muted">—</td>;
                  return (
                    <td key={c}>
                      <a href={cell.attempt_id ? `#/attempt/${cell.attempt_id}` : `#/plant/${p.mnemonic}`} className={`cell ${cell.state}`}
                        title={`${STATE_LABEL[cell.state]}\nLast poll ${fmt(cell.last_poll)}\nLast valid ${fmt(cell.last_valid)}${cell.off_reason ? "\n" + cell.off_reason : ""}${cell.error ? "\n" + cell.error : ""}${cell.attempt_id ? "\nClick to see what in the response caused this" : ""}`}>
                        <i className={`dot ${cell.state}`} />
                        <span>{STATE_LABEL[cell.state]}</span>
                        <small>{cell.last_poll ? fmt(cell.last_poll).slice(11) : ""}{cell.major_flags ? ` · ${cell.major_flags} major` : ""}{cell.minor_flags ? ` · ${cell.minor_flags} minor` : ""}</small>
                      </a>
                    </td>
                  );
                })}
                <td>{p.open_alerts ? <a href={`#/alerts`} className="badge red">{p.open_alerts}</a> : <span className="muted">0</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
