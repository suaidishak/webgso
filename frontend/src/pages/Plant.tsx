import { useEffect, useState } from "react";
import { api, CATS, CAT_LABEL, fmt, User } from "../api";
import LineChart from "../LineChart";
import Icon, { Skeleton, Spinner } from "../Icon";

type Flag = { rule: string; severity: string; message: string; ts: string };

function Flags({ flags }: { flags: Flag[] }) {
  if (!flags.length) return <p className="okline">No compliance flags on this response.</p>;
  return (
    <ul className="flags">
      {flags.sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "major" ? -1 : 1)).map((f, i) => (
        <li key={i}><span className={`sev ${f.severity}`}>{f.severity}</span><code>{f.rule}</code> {f.message}</li>
      ))}
    </ul>
  );
}

function Forecast({ mnem, cat }: { mnem: string; cat: string }) {
  const [d, setD] = useState<any>(null);
  useEffect(() => { setD(null); api(`/plants/${mnem}/forecast/${cat}`).then(setD).catch(() => setD({ snapshot: null })); }, [mnem, cat]);
  if (!d) return <p className="muted">Loading…</p>;
  if (!d.snapshot) return <p className="muted">No {CAT_LABEL[cat]} forecast stored yet.</p>;
  const s = d.snapshot;
  return (
    <>
      <div className="facts">
        <div><span>Retrieved</span>{fmt(s.retrieved_at)}</div>
        <div><span>Horizon</span>{fmt(s.horizon_start)} → {fmt(s.horizon_end)}</div>
        <div><span>Expected start</span>{fmt(s.expected_start)}</div>
        <div><span>Points</span>{s.point_count} served / {s.expected_count} expected</div>
        <div><span>Status</span><b className={s.is_valid ? "good" : "bad"}>{s.is_valid ? "Valid" : "Non-compliant"}</b></div>
        <div><span>Evidence</span><a href={`#/attempt/${s.poll_attempt_id}`}>Inspect raw JSON #{s.raw_response_id}</a></div>
      </div>
      <LineChart points={d.points} />
      <h4>Flags on this snapshot</h4>
      <Flags flags={d.flags} />
    </>
  );
}

function Mmf({ mnem }: { mnem: string }) {
  const [d, setD] = useState<any>(null);
  useEffect(() => { api(`/plants/${mnem}/mmf/latest`).then(setD); }, [mnem]);
  if (!d) return <p className="muted">Loading…</p>;
  if (!d.attempt) return <p className="muted">No MMF poll yet.</p>;
  const stations = Array.from(new Set<number>(d.readings.map((r: any) => r.station))).sort();
  const params: Record<string, any> = {};
  d.readings.forEach((r: any) => { (params[r.parameter] ||= { unit: r.unit, time: r.time, standard: r.standard, v: {} }).v[r.station] = r.value; });
  return (
    <>
      <div className="facts">
        <div><span>Polled</span>{fmt(d.attempt.started_at)}</div>
        <div><span>HTTP</span>{d.attempt.http_status ?? "—"} in {d.attempt.latency_ms} ms</div>
        <div><span>Status</span><b className={d.attempt.is_valid ? "good" : "bad"}>{d.attempt.is_valid ? "Valid" : "Non-compliant"}</b></div>
        {d.attempt.raw_response_id && <div><span>Evidence</span><a href={`#/attempt/${d.attempt.id}`}>Inspect raw JSON #{d.attempt.raw_response_id}</a></div>}
      </div>
      {stations.length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Parameter</th><th>Unit</th><th>Time</th>{stations.map(s => <th key={s} className="num">MMF{s}</th>)}</tr></thead>
          <tbody>{Object.entries(params).map(([p, x]: any) => (
            <tr key={p} className={x.standard ? "" : "muted"}><td>{p}{!x.standard && " (extra)"}</td><td>{x.unit}</td><td>{fmt(x.time).slice(11)}</td>
              {stations.map(s => <td key={s} className="num">{x.v[s] ?? "—"}</td>)}</tr>
          ))}</tbody>
        </table></div>
      )}
      <h4>Flags on this poll</h4>
      <Flags flags={d.flags} />
    </>
  );
}

export default function PlantPage({ mnem, user }: { mnem: string; user: User }) {
  const [plant, setPlant] = useState<any>(null);
  const [cat, setCat] = useState("rolling");
  const [attempts, setAttempts] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const [err, setErr] = useState("");
  const [pollErr, setPollErr] = useState("");
  useEffect(() => { api(`/plants/${mnem}`).then(setPlant).catch(e => setErr(e.message)); }, [mnem]);
  useEffect(() => { api(`/plants/${mnem}/attempts?category=${cat}&limit=20`).then(setAttempts); }, [mnem, cat, tick]);
  if (err) return <div className="error">{err}</div>;
  if (!plant) return <Skeleton rows={6} />;
  const ep = plant.endpoints.find((e: any) => e.category === cat);
  const pollNow = async () => {
    setBusy(true); setPollErr("");
    try { await api(`/admin/plants/${mnem}/poll?category=${cat}`, { method: "POST" }); setTick(t => t + 1); }
    catch (e: any) { setPollErr(`Poll failed: ${e.message}`); }
    setBusy(false);
  };
  return (
    <>
      <div className="title-row">
        <h2 className="crumbs"><a href="#/">Fleet</a><span className="sep" aria-hidden="true">/</span>{plant.mnemonic.toUpperCase()}</h2>
        {plant.capacity_mw && <span className="badge">{plant.capacity_mw} MW</span>}
        {plant.name && plant.name.toLowerCase() !== plant.mnemonic && <span className="muted">{plant.name}</span>}
        {user.role === "gso_admin" && <a className="button ghost push-right" href={`#/admin/plants/${plant.mnemonic}`}>Edit plant</a>}
      </div>
      <div className="tabs" role="tablist" aria-label="Endpoint">
        {CATS.map(c => <button key={c} role="tab" aria-selected={c === cat} aria-controls="ep-panel" className={c === cat ? "on" : ""} onClick={() => { setCat(c); setPollErr(""); }}>{CAT_LABEL[c]}</button>)}
      </div>
      <div className="card" id="ep-panel" role="tabpanel">
        <div className="endpoint">
          <code>{ep?.url}</code>
          {ep && !ep.enabled && <span className="badge">not polled{ep.note ? `: ${ep.note}` : ""}</span>}
          {user.role === "gso_admin" && ep && <button onClick={pollNow} disabled={busy}>{busy ? <><Spinner />Polling…</> : <><Icon name="zap" />Poll now</>}</button>}
        </div>
        {pollErr && <div className="error" role="alert">{pollErr}</div>}
        <div key={tick}>{cat === "mmf" ? <Mmf mnem={mnem} /> : <Forecast mnem={mnem} cat={cat} />}</div>
      </div>
      <h3>Recent polls</h3>
      <div className="table-wrap"><table>
        <thead><tr><th>Started</th><th>Scheduled</th><th>HTTP</th><th className="num">Latency</th><th className="num">Size</th><th>Result</th><th>Error</th><th></th></tr></thead>
        <tbody>{!attempts.length && <tr><td colSpan={8} className="muted">No polls of this endpoint yet.</td></tr>}{attempts.map(a => (
          <tr key={a.id}><td>{fmt(a.started_at)}</td><td>{a.scheduled_for ? fmt(a.scheduled_for).slice(11) : "manual"}</td>
            <td>{a.http_status ?? "—"}</td><td className="num">{a.latency_ms} ms</td><td className="num">{a.bytes ? `${Math.round(a.bytes / 1024)} KB` : "—"}</td>
            <td className="nowrap"><i aria-hidden="true" className={`dot ${a.http_status !== 200 ? "down" : a.is_valid ? (a.minor_flags ? "minor" : "ok") : "invalid"}`} />
              {a.major_flags} major, {a.minor_flags} minor</td><td className="small">{a.error}</td>
            <td><a href={`#/attempt/${a.id}`}>Inspect</a></td></tr>
        ))}</tbody>
      </table></div>
    </>
  );
}
