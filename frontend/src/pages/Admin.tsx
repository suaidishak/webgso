import { useEffect, useState } from "react";
import { api, CAT_LABEL, fmt, User } from "../api";
import { Skeleton, Spinner } from "../Icon";
import { Pager, PlantPicker, useDebounced } from "../components";

function UserRow({ u, onSaved }: { u: User; onSaved: () => void }) {
  const [role, setRole] = useState(u.role || (u.organisation_type === "GSO" ? "gso_user" : "lss_user"));
  const [sel, setSel] = useState<string[]>(u.plants || []);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const save = async (status: string, label: string) => {
    setErr(""); setBusy(label);
    try { await api(`/admin/users/${u.id}`, { method: "PUT", body: { role, plants: role === "lss_user" ? sel : [], status } }); onSaved(); }
    catch (e: any) { setErr(e.message); }
    setBusy("");
  };
  const btn = (status: string, label: string, cls = "") =>
    <button className={cls} disabled={!!busy} onClick={() => save(status, label)}>{busy === label ? <><Spinner />{label}</> : label}</button>;
  return (
    <tr>
      <td><b>{u.name}</b><div className="small muted">{u.email}</div></td>
      <td>{u.organisation}<div className="small muted">{u.organisation_type}</div></td>
      <td><span className={`badge ${u.status === "active" ? "green" : u.status === "pending" ? "amber" : ""}`}>{u.status}</span></td>
      <td><select value={role} aria-label={`Role for ${u.name}`} onChange={e => setRole(e.target.value)}>
        <option value="gso_admin">GSO admin</option><option value="gso_user">GSO user</option><option value="lss_user">LSS user</option>
      </select></td>
      <td>{role === "lss_user" ? (
        <PlantPicker value={sel} onChange={setSel} allowAll={false} allLabel="Choose plants" label={`Plants for ${u.name}`} />
      ) : <span className="muted small">All plants</span>}</td>
      <td className="actions">
        {u.status === "pending" && btn("active", "Approve")}
        {u.status === "active" && btn("active", "Save", "ghost")}
        {u.status === "active" && btn("disabled", "Disable", "ghost danger")}
        {u.status === "disabled" && btn("active", "Re-enable", "ghost")}
        {err && <div className="error small">{err}</div>}
      </td>
    </tr>
  );
}

export function AdminUsers() {
  const [users, setUsers] = useState<User[] | null>(null);
  const load = () => api<User[]>("/admin/users").then(setUsers);
  useEffect(() => { load(); }, []);
  if (!users) return <Skeleton rows={6} />;
  const pending = users.filter(u => u.status === "pending").length;
  return (
    <>
      <div className="title-row"><h2>Users</h2>{pending > 0 && <span className="badge amber">{pending} waiting for approval</span>}</div>
      <div className="table-wrap"><table>
        <thead><tr><th>User</th><th>Organisation</th><th>Status</th><th>Role</th><th>Plants</th><th></th></tr></thead>
        <tbody>{[...users].sort((a, b) => Number(b.status === "pending") - Number(a.status === "pending")).map(u => <UserRow key={u.id + u.status} u={u} onSaved={load} />)}</tbody>
      </table></div>
    </>
  );
}

function ScheduleRow({ s, onSaved }: { s: any; onSaved: () => void }) {
  const [f, setF] = useState({ ...s });
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const dirty = JSON.stringify(f) !== JSON.stringify(s);
  const save = async () => {
    setErr(""); setSaving(true);
    const body = { plant: s.plant, category: s.category, kind: f.kind, interval_minutes: f.kind === "interval" ? Number(f.interval_minutes) : null,
      cron: f.kind === "cron" ? f.cron : null, enabled: f.enabled, timeout_s: Number(f.timeout_s), retries: Number(f.retries),
      retry_backoff_s: Number(f.retry_backoff_s), jitter_s: Number(f.jitter_s), window_start: f.window_start || null, window_end: f.window_end || null };
    try { await api(`/admin/schedules/${s.id}`, { method: "PUT", body }); onSaved(); } catch (e: any) { setErr(e.message); }
    setSaving(false);
  };
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  return (
    <tr>
      <td>{s.plant ? <span className="mnem">{s.plant.toUpperCase()}</span> : <span className="muted">All plants</span>}</td>
      <td><b>{CAT_LABEL[s.category] || s.category}</b></td>
      <td><select value={f.kind} aria-label="Schedule type" onChange={e => set("kind", e.target.value)}><option value="interval">Every N min</option><option value="cron">Cron</option></select></td>
      <td>{f.kind === "interval"
        ? <input className="narrow" type="number" min={1} value={f.interval_minutes ?? ""} onChange={e => set("interval_minutes", e.target.value)} />
        : <input className="mono" value={f.cron ?? ""} onChange={e => set("cron", e.target.value)} />}</td>
      <td><input className="narrow" type="number" value={f.timeout_s} onChange={e => set("timeout_s", e.target.value)} /></td>
      <td><input className="narrow" type="number" value={f.retries} onChange={e => set("retries", e.target.value)} /></td>
      <td><input type="checkbox" aria-label="Schedule on" checked={f.enabled} onChange={e => set("enabled", e.target.checked)} /></td>
      <td className="small">{s.next_runs.slice(0, 3).map((t: string) => fmt(t).slice(5)).join(", ") || "—"}</td>
      <td>{dirty && <span className="actions"><button className="small-btn" disabled={saving} onClick={save}>{saving ? <><Spinner />Saving</> : "Save"}</button>
        <button className="ghost small-btn" disabled={saving} onClick={() => setF({ ...s })}>Undo</button></span>}{err && <div className="error small">{err}</div>}</td>
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
      {!rows ? <Skeleton rows={5} /> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Scope</th><th>Endpoint</th><th>Type</th><th>Every / cron</th><th>Timeout s</th><th>Retries</th><th>On</th><th>Next runs</th><th></th></tr></thead>
          <tbody>{rows.map(s => <ScheduleRow key={s.id + String(s.updated_at)} s={s} onSaved={load} />)}</tbody>
        </table></div>
      )}
      <h3>Publish deadlines</h3>
      <p className="muted small">Kept separate from pull times, so moving a pull never changes what counts as late.</p>
      <div className="table-wrap"><table>
        <thead><tr><th>Endpoint</th><th>Rule</th><th className="num">Grace</th><th>On</th></tr></thead>
        <tbody>{deadlines.map(d => (
          <tr key={d.id}><td>{CAT_LABEL[d.category]}</td>
            <td>{d.period === "daily" ? "Daily" : d.period === "weekly" ? `Every ${DAYS[d.weekday]}` : `Monthly on the ${d.day_of_month}th`} by {d.due_time.slice(0, 5)}</td>
            <td className="num">{d.grace_minutes} min</td><td>{d.enabled ? <span className="badge green">On</span> : <span className="badge">Off</span>}</td></tr>
        ))}</tbody>
      </table></div>
    </>
  );
}

export function AdminAudit() {
  const [rows, setRows] = useState<any[] | null>(null);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [size, setSize] = useState(100);
  const [err, setErr] = useState("");
  const dq = useDebounced(q);
  useEffect(() => { setOffset(0); }, [dq]);
  useEffect(() => {
    let live = true;
    const p = new URLSearchParams({ limit: String(size), offset: String(offset) });
    if (dq.trim()) p.set("q", dq.trim());
    api(`/admin/audit?${p}`).then(d => { if (live) { setRows(d.rows); setTotal(d.total); setErr(""); } }).catch(e => live && setErr(e.message));
    return () => { live = false; };
  }, [dq, offset, size]);
  if (!rows) return err ? <div className="error" role="alert">{err}</div> : <Skeleton rows={8} />;
  return (
    <>
      <div className="title-row">
        <h2>Audit log</h2><span className="muted small">{total.toLocaleString()} entries{dq ? " match" : ""}</span>
        <input className="search push-right" type="search" placeholder="Filter by user, action, target or change" aria-label="Filter audit log" value={q} onChange={e => setQ(e.target.value)} />
      </div>
      {err && <div className="error" role="alert">{err}</div>}
      <div className="table-wrap"><table>
        <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Change</th></tr></thead>
        <tbody>{!rows.length && <tr><td colSpan={5} className="muted">No entries match.</td></tr>}{rows.map(a => (
          <tr key={a.id}><td className="nowrap">{fmt(a.ts)}</td><td>{a.user}</td><td><code>{a.action}</code></td><td>{a.entity} {a.entity_id}</td>
            <td className="small mono url-cell" title={JSON.stringify(a.after, null, 2)}>{JSON.stringify(a.after)?.slice(0, 160)}{(JSON.stringify(a.after)?.length || 0) > 160 ? "…" : ""}</td></tr>
        ))}</tbody>
      </table></div>
      <Pager total={total} offset={offset} limit={size} noun="entries" sizes={[50, 100, 250, 500]} onOffset={setOffset} onLimit={setSize} />
    </>
  );
}
