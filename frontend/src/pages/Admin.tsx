import { useEffect, useState } from "react";
import { api, CAT_LABEL, fmt, User } from "../api";

function UserRow({ u, plants, onSaved }: { u: User; plants: string[]; onSaved: () => void }) {
  const [role, setRole] = useState(u.role || (u.organisation_type === "GSO" ? "gso_user" : "lss_user"));
  const [sel, setSel] = useState<string[]>(u.plants || []);
  const [err, setErr] = useState("");
  const save = async (status: string) => {
    setErr("");
    try { await api(`/admin/users/${u.id}`, { method: "PUT", body: { role, plants: role === "lss_user" ? sel : [], status } }); onSaved(); }
    catch (e: any) { setErr(e.message); }
  };
  return (
    <tr>
      <td><b>{u.name}</b><div className="small muted">{u.email}</div></td>
      <td>{u.organisation}<div className="small muted">{u.organisation_type}</div></td>
      <td><span className={`badge ${u.status === "active" ? "green" : u.status === "pending" ? "amber" : ""}`}>{u.status}</span></td>
      <td><select value={role} onChange={e => setRole(e.target.value)}>
        <option value="gso_admin">GSO admin</option><option value="gso_user">GSO user</option><option value="lss_user">LSS user</option>
      </select></td>
      <td>{role === "lss_user" ? (
        <div className="chips">{plants.map(p => (
          <label key={p} className={sel.includes(p) ? "chip on" : "chip"}>
            <input type="checkbox" checked={sel.includes(p)} onChange={e => setSel(e.target.checked ? [...sel, p] : sel.filter(x => x !== p))} />{p.toUpperCase()}
          </label>))}</div>
      ) : <span className="muted small">All plants</span>}</td>
      <td className="actions">
        {u.status === "pending" && <button onClick={() => save("active")}>Approve</button>}
        {u.status === "active" && <button className="ghost" onClick={() => save("active")}>Save</button>}
        {u.status === "active" && <button className="ghost danger" onClick={() => save("disabled")}>Disable</button>}
        {u.status === "disabled" && <button className="ghost" onClick={() => save("active")}>Re-enable</button>}
        {err && <div className="error small">{err}</div>}
      </td>
    </tr>
  );
}

export function AdminUsers() {
  const [users, setUsers] = useState<User[] | null>(null);
  const [plants, setPlants] = useState<string[]>([]);
  const load = () => api<User[]>("/admin/users").then(setUsers);
  useEffect(() => { load(); api<any[]>("/plants").then(p => setPlants(p.map(x => x.mnemonic))); }, []);
  if (!users) return <p className="muted">Loading…</p>;
  const pending = users.filter(u => u.status === "pending").length;
  return (
    <>
      <div className="title-row"><h2>Users</h2>{pending > 0 && <span className="badge amber">{pending} waiting for approval</span>}</div>
      <div className="table-wrap"><table>
        <thead><tr><th>User</th><th>Organisation</th><th>Status</th><th>Role</th><th>Plants</th><th></th></tr></thead>
        <tbody>{users.map(u => <UserRow key={u.id + u.status} u={u} plants={plants} onSaved={load} />)}</tbody>
      </table></div>
    </>
  );
}

function ScheduleRow({ s, onSaved }: { s: any; onSaved: () => void }) {
  const [f, setF] = useState({ ...s });
  const [err, setErr] = useState("");
  const dirty = JSON.stringify(f) !== JSON.stringify(s);
  const save = async () => {
    setErr("");
    const body = { plant: s.plant, category: s.category, kind: f.kind, interval_minutes: f.kind === "interval" ? Number(f.interval_minutes) : null,
      cron: f.kind === "cron" ? f.cron : null, enabled: f.enabled, timeout_s: Number(f.timeout_s), retries: Number(f.retries),
      retry_backoff_s: Number(f.retry_backoff_s), jitter_s: Number(f.jitter_s), window_start: f.window_start || null, window_end: f.window_end || null };
    try { await api(`/admin/schedules/${s.id}`, { method: "PUT", body }); onSaved(); } catch (e: any) { setErr(e.message); }
  };
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  return (
    <tr>
      <td>{s.plant ? s.plant.toUpperCase() : <span className="muted">All plants</span>}</td>
      <td><b>{CAT_LABEL[s.category] || s.category}</b></td>
      <td><select value={f.kind} onChange={e => set("kind", e.target.value)}><option value="interval">Every N min</option><option value="cron">Cron</option></select></td>
      <td>{f.kind === "interval"
        ? <input className="narrow" type="number" min={1} value={f.interval_minutes ?? ""} onChange={e => set("interval_minutes", e.target.value)} />
        : <input className="mono" value={f.cron ?? ""} onChange={e => set("cron", e.target.value)} />}</td>
      <td><input className="narrow" type="number" value={f.timeout_s} onChange={e => set("timeout_s", e.target.value)} /></td>
      <td><input className="narrow" type="number" value={f.retries} onChange={e => set("retries", e.target.value)} /></td>
      <td><input type="checkbox" checked={f.enabled} onChange={e => set("enabled", e.target.checked)} /></td>
      <td className="small">{s.next_runs.slice(0, 3).map((t: string) => fmt(t).slice(5)).join(", ") || "—"}</td>
      <td>{dirty && <button onClick={save}>Save</button>}{err && <div className="error small">{err}</div>}</td>
    </tr>
  );
}

export function AdminSchedules() {
  const [rows, setRows] = useState<any[] | null>(null);
  const [deadlines, setDeadlines] = useState<any[]>([]);
  const load = () => { api("/admin/schedules").then(setRows); api("/admin/deadlines").then(setDeadlines); };
  useEffect(load, []);
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  return (
    <>
      <h2>Poll schedules</h2>
      <p className="muted small">Pull times are data: changes take effect on the scheduler's next tick (within 30 s), no redeploy. Times are Malaysia time. Cron is minute hour day month weekday, for example <code>5 10-23 * * *</code> = 10:05, then hourly until 23:05.</p>
      {!rows ? <p className="muted">Loading…</p> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Scope</th><th>Endpoint</th><th>Type</th><th>Every / cron</th><th>Timeout s</th><th>Retries</th><th>On</th><th>Next runs</th><th></th></tr></thead>
          <tbody>{rows.map(s => <ScheduleRow key={s.id + String(s.updated_at)} s={s} onSaved={load} />)}</tbody>
        </table></div>
      )}
      <h3>Publish deadlines</h3>
      <p className="muted small">Kept separate from pull times, so moving a pull never changes what counts as late.</p>
      <div className="table-wrap"><table>
        <thead><tr><th>Endpoint</th><th>Rule</th><th>Grace</th><th>On</th></tr></thead>
        <tbody>{deadlines.map(d => (
          <tr key={d.id}><td>{CAT_LABEL[d.category]}</td>
            <td>{d.period === "daily" ? "Daily" : d.period === "weekly" ? `Every ${DAYS[d.weekday]}` : `Monthly on the ${d.day_of_month}th`} by {d.due_time.slice(0, 5)}</td>
            <td>{d.grace_minutes} min</td><td>{d.enabled ? "Yes" : "No"}</td></tr>
        ))}</tbody>
      </table></div>
    </>
  );
}

export function AdminAudit() {
  const [rows, setRows] = useState<any[] | null>(null);
  useEffect(() => { api("/admin/audit").then(setRows); }, []);
  if (!rows) return <p className="muted">Loading…</p>;
  return (
    <>
      <h2>Audit log</h2>
      <div className="table-wrap"><table>
        <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Change</th></tr></thead>
        <tbody>{rows.map(a => (
          <tr key={a.id}><td>{fmt(a.ts)}</td><td>{a.user}</td><td><code>{a.action}</code></td><td>{a.entity} {a.entity_id}</td>
            <td className="small mono">{JSON.stringify(a.after)?.slice(0, 160)}</td></tr>
        ))}</tbody>
      </table></div>
    </>
  );
}
