import { useState } from "react";
import { api, setToken } from "../api";
import { Spinner } from "../Icon";

/** GSO-branded frame shared by sign-in and registration: brand panel + form. */
function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="auth">
      <div className="auth-frame">
        <aside className="auth-brand">
          <div className="auth-plate"><img className="auth-logo" src="/brand/gso-logo.png" alt="Grid System Operator" width={226} height={100} /></div>
          <div className="auth-pitch">
            <h2>Solar Data Platform</h2>
            <p>Forecast and MMF data from every Large Scale Solar plant, collected, checked against the web service spec and kept in one place.</p>
            <ul>
              <li>Rolling, day-ahead, week-ahead and four-month forecasts</li>
              <li>Live MMF with compliance checks and alerts</li>
              <li>Reports and CSV export for each plant</li>
            </ul>
          </div>
          <p className="auth-foot">Grid System Operator · Malaysia</p>
        </aside>
        <div className="auth-main">
          <img className="auth-logo-sm logo-light" src="/brand/gso-logo.png" alt="Grid System Operator" width={170} height={75} />
          <img className="auth-logo-sm logo-dark" src="/brand/gso-logo-dark.png" alt="Grid System Operator" width={170} height={75} />
          {children}
        </div>
      </div>
    </div>
  );
}

export function Login({ onLogin, notice = "" }: { onLogin: () => void; notice?: string }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(""); setBusy(true);
    try {
      const r = await api<{ token: string }>("/auth/login", { method: "POST", body: { email, password } });
      setToken(r.token);
      // Back to the page the user was on when the session ended (not the login or register page).
      if (/^#\/(login|register)?$/.test(location.hash) || !location.hash) location.hash = "#/";
      onLogin();
    } catch (x: any) { setErr(x.message); }
    setBusy(false);
  };
  return (
    <AuthShell>
      <form className="auth-form" onSubmit={submit}>
        <div><h1>Sign in</h1><p className="muted">Use your work account for the GSO Solar Data Platform.</p></div>
        {notice && !err && <div className="notice-box" role="status">{notice}</div>}
        <label>Email<input type="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} required autoFocus /></label>
        <label>Password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>
        {err && <div className="error" role="alert">{err}</div>}
        <button disabled={busy}>{busy ? <><Spinner />Signing in…</> : "Sign in"}</button>
        <p className="muted small">No account? <a href="#/register">Register</a>. SAE Malaysia approves new accounts.</p>
      </form>
    </AuthShell>
  );
}

export function Register() {
  const [f, setF] = useState({ name: "", email: "", password: "", company: "", company_type: "LSS" });
  const [err, setErr] = useState("");
  const [done, setDone] = useState("");
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr("");
    try { const r = await api("/auth/register", { method: "POST", body: f }); setDone(r.message); }
    catch (x: any) { setErr(x.message); }
  };
  if (done) return (
    <AuthShell><div className="auth-form"><h1>Registration received</h1><p>{done}</p><a href="#/login">Back to sign in</a></div></AuthShell>
  );
  return (
    <AuthShell>
      <form className="auth-form" onSubmit={submit}>
        <div><h1>Create an account</h1><p className="muted">For GSO staff and LSS plant operators.</p></div>
        <label>Full name<input autoComplete="name" value={f.name} onChange={set("name")} required /></label>
        <label>Work email<input type="email" autoComplete="email" value={f.email} onChange={set("email")} required /></label>
        <label>Company<input value={f.company} onChange={set("company")} required /></label>
        <label>Organisation type
          <select value={f.company_type} onChange={set("company_type")}>
            <option value="LSS">LSS plant operator</option>
            <option value="GSO">GSO</option>
          </select>
        </label>
        <label>Password (10+ characters)<input type="password" autoComplete="new-password" value={f.password} onChange={set("password")} minLength={10} required /></label>
        {err && <div className="error" role="alert">{err}</div>}
        <button>Register</button>
        <p className="muted small"><a href="#/login">Back to sign in</a></p>
      </form>
    </AuthShell>
  );
}
