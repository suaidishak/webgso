import { useEffect, useMemo, useState } from "react";
import { api, CAT_LABEL, CATS, fmt, User } from "../api";
import Icon, { Skeleton, Spinner } from "../Icon";
import { Pager, PlantPicker, plantsLabel, regionsOf, useDebounced, usePlantIndex } from "../components";
import type { RecipientList } from "./Recipients";

/* ---------------------------------------------------------------- users (super admin only)
   SAE Malaysia's super admins decide each email's role: Super admin (manages users), GSO (sees every
   plant and runs the platform) or Plant user (sees only the plant acronyms picked here, read-only). */

const ROLE_OPTIONS = [
  { value: "gso", label: "GSO", help: "Sees every plant. Runs the platform: plants, schedules, Retrieve now, email reports. Can't manage users." },
  { value: "plant_user", label: "Plant user", help: "Sees only the plant acronyms you pick (one or more): their APIs, submissions, alerts, compliance and reports. Read-only." },
  { value: "super_admin", label: "Super admin", help: "SAE Malaysia. Everything GSO can do, plus this Users page." },
];
const roleHelp = (r: string) => ROLE_OPTIONS.find(o => o.value === r)?.help || "";

function TempPassword({ who, pw, onClose }: { who: string; pw: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="card temp-pw" role="status">
      <div>Temporary password for <b>{who}</b>: <code className="mono">{pw}</code>
        <button className="ghost small-btn" onClick={() => { navigator.clipboard?.writeText(pw).then(() => setCopied(true), () => {}); }}>{copied ? "Copied" : "Copy"}</button>
        <button className="ghost small-btn" onClick={onClose}>Done</button></div>
      <div className="small muted">It is shown only once. Send it to them privately; they can change it under their name at the top right.</div>
    </div>
  );
}

function UserRow({ u, me, onSaved, onPassword }: { u: User; me: number; onSaved: () => void; onPassword: (who: string, pw: string) => void }) {
  const [role, setRole] = useState(u.role || (u.organisation_type === "GSO" ? "gso" : "plant_user"));
  const [sel, setSel] = useState<string[]>(u.plants || []);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const self = u.id === me;
  const save = async (status: string, label: string) => {
    setErr(""); setBusy(label);
    try { await api(`/admin/users/${u.id}`, { method: "PUT", body: { role, plants: role === "plant_user" ? sel : [], status } }); onSaved(); }
    catch (e: any) { setErr(e.message); }
    setBusy("");
  };
  const reset = async () => {
    if (!confirm(`Give ${u.email} a new temporary password? Their current password stops working.`)) return;
    setErr(""); setBusy("Reset");
    try { const r = await api<{ temporary_password: string }>(`/admin/users/${u.id}/reset-password`, { method: "POST" }); onPassword(u.email, r.temporary_password); }
    catch (e: any) { setErr(e.message); }
    setBusy("");
  };
  const btn = (status: string, label: string, cls = "") =>
    <button className={cls} disabled={!!busy} onClick={() => save(status, label)}>{busy === label ? <><Spinner />{label}</> : label}</button>;
  return (
    <tr>
      <td><b>{u.name}</b>{self && <span className="muted small"> (you)</span>}<div className="small muted">{u.email}</div></td>
      <td>{u.organisation}<div className="small muted">{u.last_login_at ? `Last login ${fmt(u.last_login_at)}` : "Never logged in"}</div></td>
      <td><span className={`badge ${u.status === "active" ? "green" : u.status === "pending" ? "amber" : ""}`}>{u.status}</span></td>
      <td><select value={role} disabled={self} aria-label={`Role for ${u.name}`} title={roleHelp(role)} onChange={e => setRole(e.target.value)}>
        {ROLE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select></td>
      <td>{role === "plant_user" ? (
        <PlantPicker value={sel} onChange={setSel} allowAll={false} allLabel="Choose plant acronyms" label={`Plant acronyms for ${u.name}`} />
      ) : <span className="muted small">All plants</span>}</td>
      <td className="actions">
        {u.status === "pending" && btn("active", "Approve")}
        {u.status === "active" && btn("active", "Save", "ghost")}
        {u.status === "active" && !self && btn("disabled", "Disable", "ghost danger")}
        {u.status === "disabled" && btn("active", "Re-enable", "ghost")}
        {u.status !== "pending" && <button className="ghost" disabled={!!busy} onClick={reset} title="Give this user a new temporary password">{busy === "Reset" ? <><Spinner />Reset</> : "Reset password"}</button>}
        {err && <div className="error small">{err}</div>}
      </td>
    </tr>
  );
}

function NewUser({ onClose, onCreated }: { onClose: () => void; onCreated: (who: string, pw: string) => void }) {
  const [f, setF] = useState({ name: "", email: "", organisation: "", role: "plant_user" });
  const [plants, setPlants] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const set = (k: string, v: string) => setF(x => ({ ...x, [k]: v }));
  const save = async () => {
    setErr(""); setSaving(true);
    try {
      const r = await api<{ temporary_password: string }>("/admin/users", { method: "POST", body: {
        ...f, organisation: f.organisation.trim() || null, plants: f.role === "plant_user" ? plants : [] } });
      onCreated(f.email.toLowerCase(), r.temporary_password);
    } catch (e: any) { setErr(e.message); }
    setSaving(false);
  };
  const ready = f.name.trim().length >= 2 && /\S+@\S+\.\S+/.test(f.email) && (f.role !== "plant_user" || plants.length > 0);
  return (
    <div className="modal" onClick={() => !saving && onClose()}>
      <div className="card wide-modal" role="dialog" aria-modal="true" aria-labelledby="nu-title" onClick={e => e.stopPropagation()}>
        <h3 id="nu-title">Add user</h3>
        <p className="small muted" style={{ margin: 0 }}>The account is active straight away with a temporary password, shown once after you add it.</p>
        <div className="form-grid">
          <label className="field"><span>Name</span><input value={f.name} autoFocus onChange={e => set("name", e.target.value)} /></label>
          <label className="field"><span>Email</span><input type="email" value={f.email} placeholder="name@company.com" onChange={e => set("email", e.target.value)} /></label>
          <label className="field"><span>Role</span>
            <select value={f.role} onChange={e => set("role", e.target.value)}>
              {ROLE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
            <span className="small muted" style={{ fontWeight: 400 }}>{roleHelp(f.role)}</span></label>
          <label className="field"><span>Organisation {f.role === "plant_user" ? "(plant owner company, optional)" : "(optional)"}</span>
            <input value={f.organisation} placeholder={f.role === "gso" ? "GSO" : f.role === "super_admin" ? "SAE Malaysia" : "For example: ABC Solar Sdn Bhd"}
              onChange={e => set("organisation", e.target.value)} /></label>
          {f.role === "plant_user" && <div className="field wide"><span>Plant acronyms this user can see (one or more)</span>
            <PlantPicker value={plants} onChange={setPlants} allowAll={false} allLabel="Choose plant acronyms" label="Plant acronyms" /></div>}
        </div>
        {err && <div className="error small" role="alert">{err}</div>}
        <div className="actions">
          <button disabled={saving || !ready} onClick={save}>{saving ? <><Spinner />Adding</> : "Add user"}</button>
          <button className="ghost" disabled={saving} onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

export function AdminUsers({ me }: { me: number }) {
  const [users, setUsers] = useState<User[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [pw, setPw] = useState<{ who: string; pw: string } | null>(null);
  const [q, setQ] = useState("");
  const [roleF, setRoleF] = useState("");
  const load = () => api<User[]>("/admin/users").then(setUsers);
  useEffect(() => { load(); }, []);
  if (!users) return <Skeleton rows={6} />;
  const pending = users.filter(u => u.status === "pending").length;
  const ql = q.trim().toLowerCase();
  const shown = users.filter(u => (!roleF || (roleF === "pending" ? u.status === "pending" : u.role === roleF)) &&
    (!ql || [u.name, u.email, u.organisation || "", ...(u.plants || [])].some(x => x.toLowerCase().includes(ql))));
  const count = (r: string) => users.filter(u => u.role === r).length;
  return (
    <>
      <div className="title-row"><h2>Users</h2>{pending > 0 && <span className="badge amber">{pending} waiting for approval</span>}
        <button className="push-right" onClick={() => setAdding(true)}><Icon name="plus" />Add user</button></div>
      <p className="muted small">Only SAE Malaysia's super admins see this page. <b>GSO</b> sees every plant and runs the platform. A <b>plant user</b> sees
        only the plant acronyms picked for them (one or more), read-only: their APIs, submissions, alerts, compliance and reports. Role changes apply at once.</p>
      {pw && <TempPassword who={pw.who} pw={pw.pw} onClose={() => setPw(null)} />}
      <div className="user-tools">
        <input type="search" placeholder="Search name, email, organisation or plant" aria-label="Search users" value={q} onChange={e => setQ(e.target.value)} />
        <select value={roleF} aria-label="Filter by role" onChange={e => setRoleF(e.target.value)}>
          <option value="">All roles ({users.length})</option>
          {ROLE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label} ({count(o.value)})</option>)}
          <option value="pending">Waiting for approval ({pending})</option>
        </select>
      </div>
      <div className="table-wrap"><table>
        <thead><tr><th>User</th><th>Organisation</th><th>Status</th><th>Role</th><th>Plant acronyms</th><th></th></tr></thead>
        <tbody>{[...shown].sort((a, b) => Number(b.status === "pending") - Number(a.status === "pending"))
          .map(u => <UserRow key={`${u.id}-${u.status}-${u.role}-${(u.plants || []).join()}`} u={u} me={me} onSaved={load} onPassword={(who, p) => setPw({ who, pw: p })} />)}
          {!shown.length && <tr><td colSpan={6} className="muted">No users match.</td></tr>}</tbody>
      </table></div>
      {adding && <NewUser onClose={() => setAdding(false)} onCreated={(who, p) => { setAdding(false); setPw({ who, pw: p }); load(); }} />}
    </>
  );
}

/* ---------------------------------------------------------------- retrieval schedules
   A schedule targets all plants or chosen plants, and all APIs or chosen APIs. When several match one
   plant and API, the most specific wins (backend scheduler.precedence). The list is shown in that order. */

type Schedule = {
  id: number; name: string; plants: string[] | null; categories: string[] | null; kind: "interval" | "cron";
  interval_minutes: number | null; window_start: string | null; window_end: string | null; cron: string | null;
  enabled: boolean; timeout_s: number; retries: number; retry_backoff_s: number; jitter_s: number;
  valid_from: string | null; valid_to: string | null; next_runs: string[]; in_force: boolean; priority: number;
  governs: number; matches: number; recipient_list_ids: number[] | null;
};
type Cell = { state: "scheduled" | "none" | "endpoint_off" | "no_endpoint" | "not_live"; schedule_id: number | null; also: number[]; off_reason: string | null };
type CoverageRow = { plant: string; name: string; region: string | null; status: string; cells: Record<string, Cell> };

const apisLabel = (c: string[] | null) => !c ? "All APIs" : c.map(x => CAT_LABEL[x] || x).join(", ");
const whenLabel = (s: Schedule) => s.kind === "interval"
  ? `Every ${s.interval_minutes} min${s.window_start || s.window_end ? `, ${(s.window_start || "00:00").slice(0, 5)}–${(s.window_end || "23:59").slice(0, 5)}` : ""}`
  : s.cron || "";

/** "All APIs" or a set of API chips. An empty selection means all APIs. */
export function ApiPicker({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const sel = new Set(value);
  return (
    <div className="chips" role="group" aria-label="APIs">
      <button type="button" className={`chip ${value.length ? "" : "on"}`} aria-pressed={!value.length} onClick={() => onChange([])}>All APIs</button>
      {CATS.map(c => (
        <button key={c} type="button" className={`chip ${sel.has(c) ? "on" : ""}`} aria-pressed={sel.has(c)}
          onClick={() => {
            const next = sel.has(c) ? value.filter(x => x !== c) : [...value, c];
            onChange(next.length === CATS.length ? [] : CATS.filter(x => next.includes(x)));
          }}>{CAT_LABEL[c]}</button>
      ))}
    </div>
  );
}

const BLANK: Partial<Schedule> = { name: "", plants: null, categories: null, kind: "interval", interval_minutes: 15, cron: "",
  window_start: null, window_end: null, enabled: true, timeout_s: 60, retries: 2, retry_backoff_s: 20, jitter_s: 20, valid_from: null, valid_to: null, recipient_list_ids: null };

/** Recipient lists as toggle chips (code and name). */
function ListPicker({ lists, value, onChange }: { lists: RecipientList[]; value: number[]; onChange: (v: number[]) => void }) {
  const sel = new Set(value);
  if (!lists.length) return <span className="small muted">No recipient lists yet. <a href="#/admin/recipients">Create one under Email reports</a>.</span>;
  return (
    <div className="chips wide-chips" role="group" aria-label="Recipient lists">
      {lists.map(l => (
        <button key={l.id} type="button" className={`chip ${sel.has(l.id) ? "on" : ""}`} aria-pressed={sel.has(l.id)}
          title={`${l.emails.length} address${l.emails.length === 1 ? "" : "es"}${l.enabled ? "" : " · list is off"}`}
          onClick={() => onChange(sel.has(l.id) ? value.filter(x => x !== l.id) : [...value, l.id])}>
          <b>{l.code}</b>&nbsp;{l.name}{!l.enabled && <span className="muted">&nbsp;(off)</span>}</button>
      ))}
    </div>
  );
}

function ScheduleEditor({ start, lists, onClose, onSaved }: { start: Partial<Schedule>; lists: RecipientList[]; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<any>({ ...BLANK, ...start, cron: start.cron || "", interval_minutes: start.interval_minutes ?? 15 });
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }));
  const plants: string[] = f.plants || [];
  const cats: string[] = f.categories || [];
  const listIds: number[] = f.recipient_list_ids || [];
  const save = async () => {
    setErr(""); setSaving(true);
    const body = { name: f.name, plants: plants.length ? plants : null, categories: cats.length ? cats : null, kind: f.kind,
      interval_minutes: f.kind === "interval" ? Number(f.interval_minutes) : null, cron: f.kind === "cron" ? f.cron : null,
      window_start: f.kind === "interval" && f.window_start ? f.window_start : null, window_end: f.kind === "interval" && f.window_end ? f.window_end : null,
      enabled: f.enabled, timeout_s: Number(f.timeout_s), retries: Number(f.retries), retry_backoff_s: Number(f.retry_backoff_s),
      jitter_s: Number(f.jitter_s), valid_from: f.valid_from || null, valid_to: f.valid_to || null, recipient_list_ids: listIds };
    try {
      await api(f.id ? `/admin/schedules/${f.id}` : "/admin/schedules", { method: f.id ? "PUT" : "POST", body });
      onSaved();
    } catch (e: any) { setErr(e.message); }
    setSaving(false);
  };
  const specific = plants.length
    ? `${plants.length} chosen plant${plants.length === 1 ? "" : "s"}` : "all plants";
  return (
    <div className="modal" onClick={() => !saving && onClose()}>
      <div className="card wide-modal" role="dialog" aria-modal="true" aria-labelledby="sch-title" onClick={e => e.stopPropagation()}>
        <h3 id="sch-title">{f.id ? `Edit schedule #${start.priority}` : "New retrieval schedule"}</h3>
        <div className="form-grid">
          <label className="field wide"><span>Name</span>
            <input value={f.name} autoFocus placeholder="For example: Northern plants every 10 min" onChange={e => set("name", e.target.value)} /></label>
          <div className="field"><span>Plants</span>
            <PlantPicker value={plants} onChange={v => set("plants", v)} allLabel="All plants" label="Plants for this schedule" /></div>
          <div className="field"><span>APIs</span><ApiPicker value={cats} onChange={v => set("categories", v)} /></div>
          <label className="field"><span>Type</span>
            <select value={f.kind} onChange={e => set("kind", e.target.value)}><option value="interval">Every N minutes</option><option value="cron">Cron (set times)</option></select></label>
          {f.kind === "interval" ? (
            <div className="row3">
              <label className="field"><span>Every (min)</span><input type="number" min={1} max={1440} value={f.interval_minutes ?? ""} onChange={e => set("interval_minutes", e.target.value)} /></label>
              <label className="field"><span>From (optional)</span><input type="time" value={(f.window_start || "").slice(0, 5)} onChange={e => set("window_start", e.target.value)} /></label>
              <label className="field"><span>Until (optional)</span><input type="time" value={(f.window_end || "").slice(0, 5)} onChange={e => set("window_end", e.target.value)} /></label>
            </div>
          ) : (
            <label className="field"><span>Cron (minute hour day month weekday)</span>
              <input className="mono" value={f.cron} placeholder="5 10-23 * * *" onChange={e => set("cron", e.target.value)} /></label>
          )}
          <div className="row2">
            <label className="field"><span>Timeout (s)</span><input type="number" min={5} max={600} value={f.timeout_s} onChange={e => set("timeout_s", e.target.value)} /></label>
            <label className="field"><span>Retries</span><input type="number" min={0} max={10} value={f.retries} onChange={e => set("retries", e.target.value)} /></label>
          </div>
          <div className="row2">
            <label className="field"><span>Wait between retries (s)</span><input type="number" min={0} value={f.retry_backoff_s} onChange={e => set("retry_backoff_s", e.target.value)} /></label>
            <label className="field"><span>Spread start over (s)</span><input type="number" min={0} max={600} value={f.jitter_s} onChange={e => set("jitter_s", e.target.value)} /></label>
          </div>
          <div className="row2">
            <label className="field"><span>Starts (optional)</span><input type="date" value={f.valid_from || ""} onChange={e => set("valid_from", e.target.value)} /></label>
            <label className="field"><span>Ends (optional)</span><input type="date" value={f.valid_to || ""} onChange={e => set("valid_to", e.target.value)} /></label>
          </div>
          <div className="field wide"><span>Email the report to · after each scheduled retrieval, each list gets that submission's report in the client template</span>
            <ListPicker lists={lists} value={listIds} onChange={v => set("recipient_list_ids", v)} />
            {listIds.length > 0 && f.kind === "interval" && Number(f.interval_minutes) < 60 &&
              <span className="small amber-text">This schedule runs every {f.interval_minutes} min, so each list gets an email every {f.interval_minutes} min.</span>}</div>
          <label className="toggle wide"><input type="checkbox" checked={f.enabled} onChange={e => set("enabled", e.target.checked)} /> Schedule on</label>
        </div>
        <p className="small muted">Covers {specific} and {cats.length ? cats.map(c => CAT_LABEL[c]).join(", ") : "all APIs"}.
          Where another schedule also matches, the more specific one governs (see <b>When schedules overlap</b>).
          {(f.valid_from || f.valid_to) && " Dates set: while it runs, this schedule beats an open-ended one with the same targets."}</p>
        {err && <div className="error small" role="alert">{err}</div>}
        <div className="actions">
          <button disabled={saving || !f.name.trim()} onClick={save}>{saving ? <><Spinner />Saving</> : f.id ? "Save changes" : "Create schedule"}</button>
          <button className="ghost" disabled={saving} onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

function Coverage({ schedules, filter, setFilter }: {
  schedules: Schedule[]; filter: { gaps: boolean; schedule: string }; setFilter: (p: Partial<{ gaps: boolean; schedule: string }>) => void;
}) {
  const index = usePlantIndex();
  const regions = useMemo(() => regionsOf(index || []), [index]);
  const [q, setQ] = useState("");
  const [region, setRegion] = useState("");
  const [offset, setOffset] = useState(0);
  const [size, setSize] = useState(50);
  const [data, setData] = useState<{ rows: CoverageRow[]; total: number } | null>(null);
  const [err, setErr] = useState("");
  const dq = useDebounced(q);
  const byId = useMemo(() => new Map(schedules.map(s => [s.id, s])), [schedules]);
  useEffect(() => { setOffset(0); }, [dq, region, filter.gaps, filter.schedule]);
  useEffect(() => {
    let live = true;
    const p = new URLSearchParams({ limit: String(size), offset: String(offset) });
    if (dq.trim()) p.set("q", dq.trim());
    if (region) p.set("region", region);
    if (filter.gaps) p.set("gaps", "true");
    if (filter.schedule) p.set("schedule", filter.schedule);
    api(`/admin/schedules/coverage?${p}`).then(d => { if (live) { setData(d); setErr(""); } }).catch(e => live && setErr(e.message));
    return () => { live = false; };
  }, [dq, region, filter.gaps, filter.schedule, offset, size, schedules]);

  const cell = (c: Cell) => {
    if (c.state === "no_endpoint") return <span className="muted small">No API</span>;
    if (c.state === "endpoint_off") return <span className="muted small">API off</span>;
    if (c.state === "not_live") return <span className="muted small">Not live</span>;
    if (c.state === "none") return <span className="badge red">No schedule</span>;
    const s = byId.get(c.schedule_id!);
    if (!s) return null;
    const also = c.also.map(i => byId.get(i)).filter(Boolean).map(x => `#${x!.priority} ${x!.name}`);
    const tip = `Governed by #${s.priority} ${s.name} (${whenLabel(s)})` + (also.length ? `\nAlso matches, lower priority: ${also.join("; ")}` : "")
      + (c.off_reason ? `\nNot retrieved right now: ${c.off_reason}` : "");
    const hit = filter.schedule && Number(filter.schedule) === s.id;
    return (
      <span className={`gov ${s.plants ? "specific" : ""} ${hit ? "hit" : ""} ${c.off_reason ? "paused" : ""}`} title={tip}>
        <b>#{s.priority}</b> {s.name}{c.off_reason && <span className="muted"> · paused</span>}
      </span>
    );
  };
  return (
    <>
      <div className="board-row">
        <input className="search" type="search" placeholder="Search mnemonic, name or region" aria-label="Search plants" value={q} onChange={e => setQ(e.target.value)} />
        {regions.length > 1 && <select aria-label="Region" value={region} onChange={e => setRegion(e.target.value)}>
          <option value="">All regions</option>{regions.map(r => <option key={r.region} value={r.region}>{r.region} ({r.n})</option>)}</select>}
        <select aria-label="Governed by" value={filter.schedule} onChange={e => setFilter({ schedule: e.target.value })}>
          <option value="">Any schedule</option>
          {schedules.map(s => <option key={s.id} value={s.id}>Governed by #{s.priority} {s.name}</option>)}
        </select>
        <label className="toggle"><input type="checkbox" checked={filter.gaps} onChange={e => setFilter({ gaps: e.target.checked })} /> Only plants with an API that has no schedule</label>
      </div>
      {err && <div className="error" role="alert">{err}</div>}
      {!data ? <Skeleton rows={6} /> : (
        <>
          <div className="table-wrap"><table className="coverage">
            <thead><tr><th>Plant</th><th>Region</th>{CATS.map(c => <th key={c}>{CAT_LABEL[c]}</th>)}</tr></thead>
            <tbody>
              {!data.rows.length && <tr><td colSpan={2 + CATS.length} className="muted">No plants match.</td></tr>}
              {data.rows.map(r => (
                <tr key={r.plant}>
                  <td><span className="mnem">{r.plant.toUpperCase()}</span>{r.status !== "live" && <span className="muted small"> · {r.status}</span>}</td>
                  <td className="small muted">{r.region || ""}</td>
                  {CATS.map(c => <td key={c}>{cell(r.cells[c])}</td>)}
                </tr>
              ))}
            </tbody>
          </table></div>
          <Pager total={data.total} offset={offset} limit={size} noun="plants" onOffset={setOffset} onLimit={setSize} />
        </>
      )}
    </>
  );
}

export function AdminSchedules() {
  const [data, setData] = useState<{ rows: Schedule[]; pairs: number; unscheduled: number } | null>(null);
  const [deadlines, setDeadlines] = useState<any[]>([]);
  const [editing, setEditing] = useState<Partial<Schedule> | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(0);
  const [filter, setFilterState] = useState({ gaps: false, schedule: "" });
  const [lists, setLists] = useState<RecipientList[]>([]);
  const [note, setNote] = useState("");
  const setFilter = (p: Partial<typeof filter>) => setFilterState(f => ({ ...f, ...p }));
  const load = () => {
    api("/admin/schedules").then(setData).catch(e => setErr(e.message)); api("/admin/deadlines").then(setDeadlines);
    api("/admin/recipient-lists").then(d => setLists(d.rows)).catch(() => {});
  };
  const listById = useMemo(() => new Map(lists.map(l => [l.id, l])), [lists]);
  const emailLatest = async (s: Schedule) => {
    setBusy(s.id); setErr(""); setNote("");
    try {
      const r = await api(`/admin/schedules/${s.id}/send-latest`, { method: "POST" });
      const ok = r.emails.filter((x: any) => x.status === "sent").map((x: any) => x.list);
      const bad = r.emails.filter((x: any) => x.status !== "sent").map((x: any) => `${x.list} (${x.status})`);
      setNote(`Report for the ${fmt(r.due_at)} submission of "${s.name}": ${ok.length ? `sent to ${ok.join(", ")}` : "not sent"}${bad.length ? `; not sent to ${bad.join(", ")}, see Sent emails` : ""}.`);
    } catch (e: any) { setErr(e.message); }
    setBusy(0);
  };
  useEffect(load, []);
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const showCoverage = (p: Partial<typeof filter>) => {
    setFilter({ gaps: false, schedule: "", ...p });
    document.getElementById("coverage")?.scrollIntoView({ behavior: "smooth" });
  };
  const toggle = async (s: Schedule) => {
    setBusy(s.id); setErr("");
    const { id, priority, governs, matches, next_runs, in_force, ...rest } = s as any;
    try { await api(`/admin/schedules/${id}`, { method: "PUT", body: { ...rest, enabled: !s.enabled } }); load(); } catch (e: any) { setErr(e.message); }
    setBusy(0);
  };
  const remove = async (s: Schedule) => {
    const msg = s.governs
      ? `Delete "${s.name}"? It governs ${s.governs} plant/API pair${s.governs === 1 ? "" : "s"} now. They move to the next matching schedule, or have no schedule if none matches.`
      : `Delete "${s.name}"?`;
    if (!confirm(msg)) return;
    setBusy(s.id); setErr("");
    try { await api(`/admin/schedules/${s.id}`, { method: "DELETE" }); load(); } catch (e: any) { setErr(e.message); }
    setBusy(0);
  };
  const status = (s: Schedule) => !s.enabled ? <span className="badge">Off</span>
    : !s.in_force ? <span className="badge amber" title="Outside its start and end dates">Not in dates</span>
    : <span className="badge green">On</span>;
  const governs = (s: Schedule) => {
    if (!s.enabled || !s.in_force) return <span className="muted small">—</span>;
    if (!s.matches) return <span className="muted small" title="No live plant with these APIs turned on">Nothing matches</span>;
    if (!s.governs) return <span className="badge amber" title="Every plant/API it matches is taken by a higher-priority schedule">Overridden everywhere</span>;
    return <button className="link-btn small" title="Show these plants below" onClick={() => showCoverage({ schedule: String(s.id) })}>
      {s.governs.toLocaleString()}{s.governs < s.matches ? ` of ${s.matches.toLocaleString()}` : ""}</button>;
  };
  return (
    <>
      <div className="title-row">
        <h2>Retrieval schedule</h2>
        <button className="push-right" onClick={() => setEditing({ ...BLANK })}><Icon name="plus" />New schedule</button>
      </div>
      <p className="muted small">When GSO retrieves each API from each plant. Changes take effect on the scheduler's next tick (within 30 s), no redeploy.
        Times are Malaysia time. Cron is minute hour day month weekday, for example <code>5 10-23 * * *</code> = 10:05, then hourly until 23:05.</p>

      <details className="card precedence" open>
        <summary><b>When schedules overlap</b> <span className="muted small">the most specific schedule governs each plant and API</span></summary>
        <ol className="small">
          <li>A schedule for <b>chosen plants</b> beats one for all plants, and <b>fewer plants</b> beat more.</li>
          <li>Then a schedule for <b>fewer APIs</b> beats one for more (all APIs comes last).</li>
          <li>Then a schedule with <b>start or end dates</b> beats an open-ended one, so a temporary change wins while it runs.</li>
          <li>Then the <b>newest</b> schedule wins.</li>
        </ol>
        <p className="small muted">A schedule that is off, or outside its dates, is skipped and the next one takes over. The list below is in this order:
          for any plant and API, the first schedule that matches is the one used. To stop retrieving a plant or API altogether, use Retrieval on/off on the Fleet or Plants page.</p>
      </details>

      {err && <div className="error" role="alert">{err}</div>}
      {note && <div className="ok-text small" role="status">{note} <a href="#/admin/recipients/log">Sent emails</a></div>}
      {data && data.unscheduled > 0 && (
        <div className="banner soft" role="status">
          <b>{data.unscheduled.toLocaleString()} plant/API pair{data.unscheduled === 1 ? "" : "s"} have no schedule</b> and are not retrieved automatically.
          <button className="ghost small-btn" onClick={() => showCoverage({ gaps: true })}>Show them</button>
        </div>
      )}
      {!data ? <Skeleton rows={5} /> : (
        <div className="table-wrap"><table className="schedules">
          <thead><tr><th className="num" title="Priority: lower number wins">#</th><th>Name</th><th>Plants</th><th>APIs</th><th>When</th>
            <th>Retries · timeout</th><th>Dates</th><th>Status</th><th className="num" title="Plant/API pairs this schedule decides">Governs</th><th>Next runs</th><th>Email to</th><th></th></tr></thead>
          <tbody>{data.rows.map(s => (
            <tr key={s.id} className={!s.enabled || !s.in_force ? "dim" : ""}>
              <td className="num"><b>{s.priority}</b></td>
              <td><b>{s.name}</b></td>
              <td>{s.plants === null ? <span className="muted">All plants</span>
                : !s.plants.length ? <span className="badge amber">No plants</span>
                : <span title={s.plants.map(m => m.toUpperCase()).join(", ")}>{plantsLabel(s.plants)}{s.plants.length > 1 && <span className="badge">{s.plants.length}</span>}</span>}</td>
              <td>{s.categories === null ? <span className="muted">All APIs</span> : apisLabel(s.categories)}</td>
              <td className={s.kind === "cron" ? "mono small" : ""}>{whenLabel(s)}</td>
              <td className="small nowrap">{s.retries} · {s.timeout_s} s</td>
              <td className="small nowrap">{s.valid_from || s.valid_to ? `${s.valid_from || "…"} to ${s.valid_to || "…"}` : <span className="muted">Always</span>}</td>
              <td>{status(s)}</td>
              <td className="num">{governs(s)}</td>
              <td className="small">{s.next_runs.slice(0, 3).map(t => fmt(t).slice(5)).join(", ") || "—"}</td>
              <td className="small">{s.recipient_list_ids?.length
                ? s.recipient_list_ids.map(id => { const l = listById.get(id); return l
                    ? <span key={id} className={`badge ${l.enabled ? "" : "dim-badge"}`} title={`${l.name}: ${l.emails.length} address${l.emails.length === 1 ? "" : "es"}${l.enabled ? "" : " (list is off)"}`}>{l.code}</span> : null; })
                : <span className="muted">—</span>}</td>
              <td className="actions">
                <button className="ghost small-btn" disabled={busy === s.id} onClick={() => setEditing(s)}>Edit</button>
                <button className="ghost small-btn" disabled={busy === s.id} onClick={() => toggle(s)}>{s.enabled ? "Turn off" : "Turn on"}</button>
                <button className="ghost small-btn" disabled={busy === s.id} onClick={() => setEditing({ ...s, id: undefined, name: `${s.name} (copy)` })}>Copy</button>
                {!!s.recipient_list_ids?.length && <button className="ghost small-btn" disabled={busy === s.id} title="Email the report of this schedule's latest finished retrieval now, to its lists"
                  onClick={() => emailLatest(s)}>{busy === s.id ? <Spinner /> : null}Email latest</button>}
                <button className="ghost small-btn danger" disabled={busy === s.id} onClick={() => remove(s)}>Delete</button>
              </td>
            </tr>
          ))}</tbody>
        </table></div>
      )}

      <h3 id="coverage">Which schedule governs each plant</h3>
      <p className="muted small">Every plant and API, with the schedule that decides when it is retrieved. Hover a cell to see the other schedules that also match it.
        Highlighted cells come from a schedule for chosen plants rather than all plants.</p>
      {data && <Coverage schedules={data.rows} filter={filter} setFilter={setFilter} />}

      <h3>Publish deadlines</h3>
      <p className="muted small">Kept separate from retrieval times, so moving a retrieval never changes what counts as late.</p>
      <div className="table-wrap"><table>
        <thead><tr><th>API</th><th>Rule</th><th className="num">Grace</th><th>On</th></tr></thead>
        <tbody>{deadlines.map(d => (
          <tr key={d.id}><td>{CAT_LABEL[d.category]}</td>
            <td>{d.period === "daily" ? "Daily" : d.period === "weekly" ? `Every ${DAYS[d.weekday]}` : `Monthly on the ${d.day_of_month}th`} by {d.due_time.slice(0, 5)}</td>
            <td className="num">{d.grace_minutes} min</td><td>{d.enabled ? <span className="badge green">On</span> : <span className="badge">Off</span>}</td></tr>
        ))}</tbody>
      </table></div>
      {editing && <ScheduleEditor start={editing} lists={lists} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}
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
