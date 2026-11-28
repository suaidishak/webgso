import { useState } from "react";
import { api, ROLE_LABEL, User } from "../api";
import { Spinner } from "../Icon";

const ROLE_HELP: Record<string, string> = {
  super_admin: "You manage users for SAE Malaysia and can do everything in the platform.",
  gso: "You see every plant and run the platform: plants, schedules, Retrieve now and email reports. SAE Malaysia manages users.",
  plant_user: "You see only the plants below: their APIs, submissions, alerts, compliance and reports. Your access is read-only. SAE Malaysia manages users.",
};

/** Your role, the plants you can see, and changing your password. */
export default function Account({ user }: { user: User }) {
  const [f, setF] = useState({ current: "", next: "", again: "" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (f.next !== f.again) { setMsg({ ok: false, text: "The new passwords don't match" }); return; }
    setBusy(true); setMsg(null);
    try {
      await api("/auth/me/password", { method: "PUT", body: { current: f.current, new: f.next } });
      setF({ current: "", next: "", again: "" }); setMsg({ ok: true, text: "Password changed" });
    } catch (x: any) { setMsg({ ok: false, text: x.message }); }
    setBusy(false);
  };
  return (
    <>
      <div className="title-row"><h2>Your account</h2></div>
      <div className="card">
        <p><b>{user.name}</b> <span className="muted">{user.email}</span></p>
        <p>Role: <span className="badge">{ROLE_LABEL[user.role || ""] || user.role}</span>
          {user.organisation && <span className="muted"> · {user.organisation}</span>}</p>
        <p className="muted small">{ROLE_HELP[user.role || ""]}</p>
        {user.role === "plant_user" && <p>Your plants: {(user.plants || []).length
          ? (user.plants || []).map(p => <a key={p} className="badge" href={`#/plant/${p}`} style={{ marginRight: 6 }}>{p.toUpperCase()}</a>)
          : <span className="muted">none assigned yet. Ask SAE Malaysia to assign your plant acronyms.</span>}</p>}
      </div>
      <h3>Change password</h3>
      <form className="card auth-form" style={{ maxWidth: 420 }} onSubmit={submit}>
        <label>Current password<input type="password" autoComplete="current-password" value={f.current} onChange={e => setF({ ...f, current: e.target.value })} required /></label>
        <label>New password (at least 10 characters)<input type="password" autoComplete="new-password" minLength={10} value={f.next} onChange={e => setF({ ...f, next: e.target.value })} required /></label>
        <label>New password again<input type="password" autoComplete="new-password" minLength={10} value={f.again} onChange={e => setF({ ...f, again: e.target.value })} required /></label>
        {msg && <div className={msg.ok ? "ok-text" : "error"} role={msg.ok ? "status" : "alert"}>{msg.text}</div>}
        <button disabled={busy}>{busy ? <><Spinner />Saving…</> : "Change password"}</button>
      </form>
    </>
  );
}
