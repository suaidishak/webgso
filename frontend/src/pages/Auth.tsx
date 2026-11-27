import { useState } from "react";
import { api, setToken } from "../api";
import { Spinner } from "../Icon";

export function Login({ onLogin }: { onLogin: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(""); setBusy(true);
    try {
      const r = await api<{ token: string }>("/auth/login", { method: "POST", body: { email, password } });
      setToken(r.token); location.hash = "#/"; onLogin();
    } catch (x: any) { setErr(x.message); }
    setBusy(false);
  };
  return (
    <div className="auth">
      <form className="card" onSubmit={submit}>
        <h1><span className="sun" aria-hidden="true" />GSO Solar Data Platform</h1>
        <p className="muted">Sign in with your work account.</p>
        <label>Email<input type="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} required autoFocus /></label>
        <label>Password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>
        {err && <div className="error" role="alert">{err}</div>}
        <button disabled={busy}>{busy ? <><Spinner />Signing in…</> : "Sign in"}</button>
        <p className="muted small">No account? <a href="#/register">Register</a>. A GSO admin approves new accounts.</p>
      </form>
    </div>
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
    <div className="auth"><div className="card"><h1>Registration received</h1><p>{done}</p><a href="#/login">Back to sign in</a></div></div>
  );
  return (
    <div className="auth">
      <form className="card" onSubmit={submit}>
        <h1>Create an account</h1>
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
    </div>
  );
}
