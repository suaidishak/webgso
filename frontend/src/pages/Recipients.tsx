import { Fragment, useEffect, useMemo, useState } from "react";
import { api, CAT_LABEL, fmt } from "../api";
import Icon, { Skeleton, Spinner } from "../Icon";
import { Pager, useQueryState } from "../components";
import { ApiPicker } from "./Admin";

/* ---------------------------------------------------------------- recipient lists and the sent-email log
   A recipient list has a running code (RL-001 ..), a name and one or more addresses, plus which report it
   receives. Retrieval schedules are paired with lists on the Schedules page; after each scheduled retrieval
   (never Retrieve now) every paired list is emailed the report in the client template. */

export type RecipientList = {
  id: number; code: string; name: string; emails: string[]; categories: string[] | null;
  forecast_interval: number; mmf_interval: number; mmf_source: "station" | "average"; enabled: boolean;
  notes: string | null; schedules?: { id: number; name: string; enabled: boolean }[];
  last_email?: { status: string; at: string } | null;
};
type Smtp = { configured: boolean; host: string | null; port: number; security: string; username: string | null;
  from: string; reply_to: string | null; retries: number[]; public_base_url: string; catcher: boolean };
type Email = {
  id: number; kind: "scheduled" | "resend" | "test"; schedule_id: number | null; schedule_name: string | null;
  recipient_list_id: number | null; list_code: string | null; due_at: string | null; recipients: string[];
  subject: string | null; attachments: { name: string; bytes: number }[] | null; status: string; attempts: number;
  last_error: string | null; created_at: string; next_attempt_at: string | null; sent_at: string | null;
  summary: { issues: number; apis: { category: string; plants: number; valid: number }[] } | null;
};

const apisLabel = (c: string[] | null) => !c ? "All APIs in the retrieval" : c.map(x => CAT_LABEL[x] || x).join(", ");
export const listLabel = (l: Pick<RecipientList, "code" | "name">) => `${l.code} ${l.name}`;
const STATUS: Record<string, [string, string]> = {
  sent: ["green", "Sent"], pending: ["", "Queued"], sending: ["", "Sending"], retry: ["amber", "Will retry"],
  failed: ["red", "Failed"], skipped: ["", "Nothing to send"],
};
const statusBadge = (s: string) => <span className={`badge ${STATUS[s]?.[0] || ""}`}>{STATUS[s]?.[1] || s}</span>;

function Tabs({ on }: { on: "lists" | "log" }) {
  return (
    <div className="tabs" role="tablist" aria-label="Email reports">
      <button role="tab" aria-selected={on === "lists"} className={on === "lists" ? "on" : ""} onClick={() => { location.hash = "#/admin/recipients"; }}>Recipient lists</button>
      <button role="tab" aria-selected={on === "log"} className={on === "log" ? "on" : ""} onClick={() => { location.hash = "#/admin/recipients/log"; }}>Sent emails</button>
    </div>
  );
}

const BLANK = { name: "", emails: [] as string[], categories: null as string[] | null, forecast_interval: 15, mmf_interval: 15,
  mmf_source: "station", enabled: true, notes: "" };

function ListEditor({ start, onClose, onSaved }: { start: Partial<RecipientList>; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<any>({ ...BLANK, ...start, notes: start.notes || "" });
  const [text, setText] = useState((start.emails || []).join("\n"));
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }));
  const cats: string[] = f.categories || [];
  const count = text.split(/[\n,;]+/).map(x => x.trim()).filter(Boolean).length;
  const wantsForecast = !cats.length || cats.some(c => c !== "mmf");
  const wantsMmf = !cats.length || cats.includes("mmf");
  const save = async () => {
    setErr(""); setSaving(true);
    const body = { name: f.name, emails: [text], categories: cats.length ? cats : null, forecast_interval: Number(f.forecast_interval),
      mmf_interval: Number(f.mmf_interval), mmf_source: f.mmf_source, enabled: f.enabled, notes: f.notes || null };
    try {
      await api(f.id ? `/admin/recipient-lists/${f.id}` : "/admin/recipient-lists", { method: f.id ? "PUT" : "POST", body });
      onSaved();
    } catch (e: any) { setErr(e.message.replace(/^Value error, /, "")); }
    setSaving(false);
  };
  return (
    <div className="modal" onClick={() => !saving && onClose()}>
      <div className="card wide-modal" role="dialog" aria-modal="true" aria-labelledby="rl-title" onClick={e => e.stopPropagation()}>
        <h3 id="rl-title">{f.id ? `Edit ${f.code}` : "New recipient list"}</h3>
        {!f.id && <p className="small muted" style={{ margin: 0 }}>The list gets the next running code (RL-001, RL-002 ..) when you create it. Pair it with schedules on the Schedules page.</p>}
        <div className="form-grid">
          <label className="field wide"><span>Name</span>
            <input value={f.name} autoFocus placeholder="For example: GSO operations desk" onChange={e => set("name", e.target.value)} /></label>
          <label className="field wide"><span>Email addresses ({count}) · one per line, or separated by commas</span>
            <textarea rows={5} className="mono" value={text} placeholder={"ops@gso.com.my\nlead@gso.com.my"} onChange={e => setText(e.target.value)} /></label>
          <div className="field wide"><span>Report to send · the APIs this list receives (only those the retrieval actually ran)</span>
            <ApiPicker value={cats} onChange={v => set("categories", v.length ? v : null)} /></div>
          {wantsForecast && <label className="field"><span>Forecast interval (DDQ template)</span>
            <select value={f.forecast_interval} onChange={e => set("forecast_interval", e.target.value)}>
              <option value={15}>15 minutes</option><option value={60}>1 hour (mean of the four 15-minute points)</option></select></label>}
          {wantsMmf && <div className="row2">
            <label className="field"><span>MMF interval</span>
              <select value={f.mmf_interval} onChange={e => set("mmf_interval", e.target.value)}>
                <option value={5}>5 minutes</option><option value={15}>15 minutes</option><option value={60}>1 hour</option></select></label>
            <label className="field"><span>MMF rows</span>
              <select value={f.mmf_source} onChange={e => set("mmf_source", e.target.value)}>
                <option value="station">Individual MMF</option><option value="average">Average of stations</option></select></label>
          </div>}
          <label className="field wide"><span>Notes (optional)</span>
            <input value={f.notes} placeholder="Who this list is for" onChange={e => set("notes", e.target.value)} /></label>
          <label className="toggle wide"><input type="checkbox" checked={f.enabled} onChange={e => set("enabled", e.target.checked)} /> List on (when off, paired schedules skip it)</label>
        </div>
        {err && <div className="error small" role="alert">{err}</div>}
        <div className="actions">
          <button disabled={saving || !f.name.trim() || !count} onClick={save}>{saving ? <><Spinner />Saving</> : f.id ? "Save changes" : "Create list"}</button>
          <button className="ghost" disabled={saving} onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

function SmtpCard({ smtp }: { smtp: Smtp }) {
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ ok: boolean; msg: string } | null>(null);
  const send = async () => {
    setBusy(true); setRes(null);
    try {
      const r = await api("/admin/email/test", { method: "POST", body: { to: [to] } });
      setRes(r.status === "sent" ? { ok: true, msg: `Test email sent to ${r.to.join(", ")}.` } : { ok: false, msg: r.error || "Not sent" });
    } catch (e: any) { setRes({ ok: false, msg: e.message }); }
    setBusy(false);
  };
  return (
    <div className="card smtp-card">
      <div className="smtp-row">
        <div>
          <span><b>Outgoing email</b>{" "}
            {smtp.configured ? <span className="badge green">Set up</span> : <span className="badge red">Not set up</span>}</span>
          <div className="small muted">
            {smtp.configured
              ? <>Server <code>{smtp.host}:{smtp.port}</code> ({smtp.security}){smtp.username ? `, signs in as ${smtp.username}` : ""}. From <code>{smtp.from}</code>.
                {" "}A failed email is tried again after {smtp.retries.join(", ")} minutes, then marked failed.</>
              : <>Set <code>SMTP_HOST</code> and the other <code>SMTP_</code> settings in <code>.env</code>, then restart. Until then report emails are logged as failed.</>}
          </div>
          {smtp.catcher && <div className="small">Local mail catcher: every email lands in Mailpit, nothing leaves your computer.{" "}
            <a href="http://localhost:8025" target="_blank" rel="noreferrer">Open Mailpit (localhost:8025)</a></div>}
        </div>
        <div className="smtp-test">
          <input type="email" aria-label="Send a test email to" placeholder="you@example.com" value={to} onChange={e => setTo(e.target.value)}
            onKeyDown={e => e.key === "Enter" && to.trim() && send()} />
          <button className="ghost" disabled={busy || !to.trim()} onClick={send}>{busy ? <><Spinner />Sending</> : "Send test email"}</button>
        </div>
      </div>
      {res && <div className={`small ${res.ok ? "ok-text" : "error"}`} role="status">{res.msg}</div>}
    </div>
  );
}

export function RecipientLists() {
  const [data, setData] = useState<{ rows: RecipientList[]; smtp: Smtp } | null>(null);
  const [editing, setEditing] = useState<Partial<RecipientList> | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(0);
  const load = () => api("/admin/recipient-lists").then(setData).catch(e => setErr(e.message));
  useEffect(() => { load(); }, []);
  const act = async (l: RecipientList, fn: () => Promise<any>) => {
    setBusy(l.id); setErr(""); setNote("");
    try { await fn(); } catch (e: any) { setErr(e.message); }
    setBusy(0);
  };
  const test = (l: RecipientList) => act(l, async () => {
    const r = await api(`/admin/recipient-lists/${l.id}/test`, { method: "POST" });
    if (r.status === "sent") setNote(`Test email sent to ${l.code} (${r.to.length} address${r.to.length === 1 ? "" : "es"}).`);
    else setErr(`Test email to ${l.code} failed: ${r.error}`);
  });
  const toggle = (l: RecipientList) => act(l, async () => {
    const { id, code, schedules, last_email, created_at, updated_at, ...rest } = l as any;
    await api(`/admin/recipient-lists/${id}`, { method: "PUT", body: { ...rest, enabled: !l.enabled } });
    load();
  });
  const remove = (l: RecipientList) => {
    const used = l.schedules?.length || 0;
    if (!confirm(`Delete ${l.code} ${l.name}?` + (used ? ` It is paired with ${used} schedule${used === 1 ? "" : "s"}, which will stop emailing it.` : "")
      + " The sent-email log keeps its history. The code is not reused.")) return;
    act(l, async () => { await api(`/admin/recipient-lists/${l.id}`, { method: "DELETE" }); load(); });
  };
  return (
    <>
      <div className="title-row">
        <h2>Email reports</h2>
        <button className="push-right" onClick={() => setEditing({ ...BLANK } as any)}><Icon name="plus" />New recipient list</button>
      </div>
      <Tabs on="lists" />
      <p className="muted small">After every <b>scheduled</b> retrieval, each recipient list paired with that schedule is emailed the report for that submission
        in the client template (one Excel file per API) with a summary of failed, invalid and missing data. Retrieve now and Test never send email.
        Pair lists with schedules under <a href="#/admin/schedules">Schedules</a> (<b>Email the report to</b>).</p>
      {data && <SmtpCard smtp={data.smtp} />}
      {err && <div className="error" role="alert">{err}</div>}
      {note && <div className="ok-text small" role="status">{note}</div>}
      {!data ? <Skeleton rows={4} /> : !data.rows.length ? (
        <div className="card empty-card"><p><b>No recipient lists yet.</b></p>
          <p className="muted small">Create a list, then pair it with one or more retrieval schedules.</p>
          <button onClick={() => setEditing({ ...BLANK } as any)}><Icon name="plus" />New recipient list</button></div>
      ) : (
        <div className="table-wrap"><table className="recipients">
          <thead><tr><th>Code</th><th>Name</th><th>Addresses</th><th>Report</th><th>Paired schedules</th><th>Last email</th><th>Status</th><th></th></tr></thead>
          <tbody>{data.rows.map(l => (
            <tr key={l.id} className={l.enabled ? "" : "dim"}>
              <td className="nowrap"><span className="mnem">{l.code}</span></td>
              <td><b>{l.name}</b>{l.notes && <div className="small muted">{l.notes}</div>}</td>
              <td className="small" title={l.emails.join("\n")}>{l.emails.slice(0, 2).join(", ")}{l.emails.length > 2 && <span className="badge">+{l.emails.length - 2}</span>}</td>
              <td className="small">{apisLabel(l.categories)}
                <div className="muted">{(!l.categories || l.categories.some(c => c !== "mmf")) && `Forecast ${l.forecast_interval} min`}
                  {(!l.categories || l.categories.some(c => c !== "mmf")) && (!l.categories || l.categories.includes("mmf")) && " · "}
                  {(!l.categories || l.categories.includes("mmf")) && `MMF ${l.mmf_interval} min, ${l.mmf_source === "average" ? "average" : "individual"}`}</div></td>
              <td className="small">{l.schedules?.length
                ? l.schedules.map(s => <div key={s.id} className={s.enabled ? "" : "muted"}>{s.name}{!s.enabled && " (off)"}</div>)
                : <span className="badge amber" title="Pair it with a schedule on the Schedules page">Not paired</span>}</td>
              <td className="small nowrap">{l.last_email
                ? <a href={`#/admin/recipients/log?list=${l.id}`}>{statusBadge(l.last_email.status)} {fmt(l.last_email.at).slice(5)}</a>
                : <span className="muted">—</span>}</td>
              <td>{l.enabled ? <span className="badge green">On</span> : <span className="badge">Off</span>}</td>
              <td className="actions">
                <button className="ghost small-btn" disabled={busy === l.id} onClick={() => setEditing(l)}>Edit</button>
                <button className="ghost small-btn" disabled={busy === l.id} onClick={() => test(l)}>{busy === l.id ? <Spinner /> : null}Send test</button>
                <button className="ghost small-btn" disabled={busy === l.id} onClick={() => toggle(l)}>{l.enabled ? "Turn off" : "Turn on"}</button>
                <button className="ghost small-btn danger" disabled={busy === l.id} onClick={() => remove(l)}>Delete</button>
              </td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {editing && <ListEditor start={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}
    </>
  );
}

/** Reports, client template, for the 10 minutes after the submission time (when its retrievals ran). */
function reportsLink(e: Email) {
  const pad = (n: number) => String(n).padStart(2, "0");
  const local = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const from = new Date(e.due_at!);
  const p = new URLSearchParams({ category: e.summary?.apis?.[0]?.category || "dayahead", from: local(from),
    to: local(new Date(from.getTime() + 10 * 60_000)) });
  return `#/reports?${p}`;
}

const kb = (n: number) => n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;

export function EmailLog({ query }: { query: string }) {
  const [q, setQ] = useQueryState("/admin/recipients/log", query, { status: "", kind: "", list: "", email: "" });
  const [offset, setOffset] = useState(0);
  const [size, setSize] = useState(50);
  const [data, setData] = useState<{ rows: Email[]; total: number; today: Record<string, number> } | null>(null);
  const [lists, setLists] = useState<RecipientList[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(0);
  const [open, setOpen] = useState<number | null>(q.email ? Number(q.email) : null);
  const [tick, setTick] = useState(0);
  useEffect(() => { api("/admin/recipient-lists").then(d => setLists(d.rows)).catch(() => {}); }, []);
  useEffect(() => { setOffset(0); }, [q.status, q.kind, q.list, q.email]);
  useEffect(() => {
    let live = true;
    const p = new URLSearchParams({ limit: String(size), offset: String(offset) });
    if (q.status) p.set("status", q.status);
    if (q.kind) p.set("kind", q.kind);
    if (q.list) p.set("list_id", q.list);
    if (q.email) p.set("email", q.email);
    api(`/admin/emails?${p}`).then(d => { if (live) { setData(d); setErr(""); } }).catch(e => live && setErr(e.message));
    return () => { live = false; };
  }, [q.status, q.kind, q.list, q.email, offset, size, tick]);
  // Queued and retrying emails change on their own: refresh every 15 s.
  useEffect(() => { const t = setInterval(() => setTick(x => x + 1), 15_000); return () => clearInterval(t); }, []);
  const resend = async (e: Email) => {
    setBusy(e.id); setErr("");
    try { await api(`/admin/emails/${e.id}/resend`, { method: "POST" }); setTick(x => x + 1); } catch (x: any) { setErr(x.message); }
    setBusy(0);
  };
  const today = data?.today || {};
  const problems = (today.failed || 0) + (today.retry || 0);
  const byId = useMemo(() => new Map(lists.map(l => [l.id, l])), [lists]);
  return (
    <>
      <div className="title-row"><h2>Email reports</h2></div>
      <Tabs on="log" />
      <p className="muted small">Every email the platform has sent or tried to send, newest first. A failed send is retried automatically; <b>Resend</b> sends
        the same submission's report again now, to the list's current addresses.</p>
      {data && <div className="board-row small">
        <span>Today: <b>{(today.sent || 0).toLocaleString()}</b> sent</span>
        {problems > 0 && <button className="link-btn" onClick={() => setQ({ status: "problem", email: "" })}><span className="badge red">{problems} failed or retrying</span></button>}
        {(today.pending || 0) > 0 && <span className="muted">{today.pending} queued</span>}
      </div>}
      <div className="board-row">
        <select aria-label="Status" value={q.status} onChange={e => setQ({ status: e.target.value, email: "" })}>
          <option value="">Any status</option><option value="sent">Sent</option><option value="problem">Failed or retrying</option>
          <option value="failed">Failed</option><option value="retry">Will retry</option><option value="pending">Queued</option><option value="skipped">Nothing to send</option>
        </select>
        <select aria-label="Type" value={q.kind} onChange={e => setQ({ kind: e.target.value, email: "" })}>
          <option value="">Any type</option><option value="scheduled">After a scheduled retrieval</option><option value="resend">Resent</option><option value="test">Test</option>
        </select>
        <select aria-label="Recipient list" value={q.list} onChange={e => setQ({ list: e.target.value, email: "" })}>
          <option value="">Any recipient list</option>{lists.map(l => <option key={l.id} value={l.id}>{listLabel(l)}</option>)}
        </select>
        {q.email && <button className="ghost small-btn" onClick={() => setQ({ email: "" })}><Icon name="x" />Showing email #{q.email} only</button>}
      </div>
      {err && <div className="error" role="alert">{err}</div>}
      {!data ? <Skeleton rows={6} /> : (
        <>
          <div className="table-wrap"><table className="email-log">
            <thead><tr><th>Created</th><th>Type</th><th>Schedule · submission</th><th>List</th><th>Subject</th><th>Status</th><th className="num">Tries</th><th></th></tr></thead>
            <tbody>
              {!data.rows.length && <tr><td colSpan={8} className="muted">No emails yet. They appear here after the first scheduled retrieval of a schedule paired with a recipient list.</td></tr>}
              {data.rows.map(e => (
                <Fragment key={e.id}>
                  <tr className={open === e.id ? "open" : ""}>
                    <td className="small nowrap">{fmt(e.created_at)}</td>
                    <td className="small">{e.kind === "scheduled" ? "Scheduled" : e.kind === "resend" ? "Resent" : "Test"}</td>
                    <td className="small">{e.schedule_name || <span className="muted">—</span>}{e.due_at && <div className="muted">{fmt(e.due_at)}</div>}</td>
                    <td className="small nowrap" title={e.recipients.join("\n")}>{e.list_code
                      ? <>{e.list_code}{e.recipient_list_id && byId.get(e.recipient_list_id) ? ` ${byId.get(e.recipient_list_id)!.name}` : ""}</>
                      : e.recipients.join(", ")}<div className="muted">{e.recipients.length} address{e.recipients.length === 1 ? "" : "es"}</div></td>
                    <td className="small">{e.subject || <span className="muted">Building…</span>}
                      {e.attachments?.length ? <div className="muted">{e.attachments.map(a => `${a.name} (${kb(a.bytes)})`).join(", ")}</div> : null}</td>
                    <td className="nowrap">{statusBadge(e.status)}
                      {e.status === "sent" && e.sent_at && <div className="small muted">{fmt(e.sent_at).slice(11)}</div>}
                      {e.status === "retry" && e.next_attempt_at && <div className="small muted">next {fmt(e.next_attempt_at).slice(11)}</div>}</td>
                    <td className="num small">{e.attempts}</td>
                    <td className="actions">
                      <button className="ghost small-btn" onClick={() => setOpen(open === e.id ? null : e.id)} aria-expanded={open === e.id}>Details</button>
                      {e.kind !== "test" && e.schedule_id && <button className="ghost small-btn" disabled={busy === e.id} onClick={() => resend(e)}>{busy === e.id ? <Spinner /> : null}Resend</button>}
                    </td>
                  </tr>
                  {open === e.id && (
                    <tr className="detail-row"><td colSpan={8}>
                      <div className="small"><b>To:</b> {e.recipients.join(", ")}</div>
                      {e.last_error && <div className="error small"><b>Last error:</b> {e.last_error}</div>}
                      {e.summary?.apis?.map(a => (
                        <div key={a.category} className="small">{CAT_LABEL[a.category] || a.category}: {a.valid} of {a.plants} plants valid</div>
                      ))}
                      {e.due_at && e.schedule_id && <div className="small"><a href={reportsLink(e)}>Open this submission in Reports</a></div>}
                    </td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table></div>
          <Pager total={data.total} offset={offset} limit={size} noun="emails" onOffset={setOffset} onLimit={setSize} />
        </>
      )}
    </>
  );
}
