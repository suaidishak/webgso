import { Fragment, useEffect, useRef, useState } from "react";
import { api, CATS, CAT_LABEL, fmt } from "../api";
import Icon, { Skeleton, Spinner } from "../Icon";

type Endpoint = { category: string; url: string; enabled: boolean; note: string | null; polled?: boolean; off_reason?: string | null };
type PlantRow = {
  mnemonic: string; name: string; capacity_mw: number | null; mmf_station_count: number | null; status: string;
  notes: string | null; endpoints: Endpoint[]; auth_method: string; auth_param_name: string | null;
  has_api_key: boolean; api_key_hint: string | null; updated_at: string | null; polling_enabled: boolean;
};
type Polling = { fleet: boolean; types: Record<string, boolean> };

export function Switch({ on, onChange, label, disabled, title }: { on: boolean; onChange: (v: boolean) => void; label?: string; disabled?: boolean; title?: string }) {
  return (
    <label className={`switch ${on ? "on" : ""} ${disabled ? "dis" : ""}`} title={title}>
      <input type="checkbox" role="switch" aria-label={label ? undefined : title} checked={on} disabled={disabled} onChange={e => onChange(e.target.checked)} />
      <span className="track"><span className="knob" /></span>
      {label && <span className="switch-label">{label}</span>}
    </label>
  );
}

function PollingPanel({ polling, onChange }: { polling: Polling; onChange: (p: Polling) => void }) {
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState("");
  const setFleet = async (enabled: boolean) => {
    setErr(""); setConfirm(false);
    try { onChange(await api("/admin/polling/fleet", { method: "PUT", body: { enabled } })); } catch (e: any) { setErr(e.message); }
  };
  const setType = async (c: string, enabled: boolean) => {
    setErr("");
    try { onChange(await api(`/admin/polling/types/${c}`, { method: "PUT", body: { enabled } })); } catch (e: any) { setErr(e.message); }
  };
  return (
    <div className={`card polling ${polling.fleet ? "" : "paused"}`}>
      <div className="polling-fleet">
        <div>
          <b>Fleet polling</b>
          <div className="small muted">{polling.fleet ? "Scheduled polling is running for every plant." : "All scheduled polling is paused. Tests and Poll now still work."}</div>
        </div>
        {confirm ? (
          <span className="actions">
            <span className="small">Pause polling for every plant?</span>
            <button className="danger-fill" onClick={() => setFleet(false)}>Pause all</button>
            <button className="ghost" onClick={() => setConfirm(false)}>Cancel</button>
          </span>
        ) : (
          <Switch on={polling.fleet} label={polling.fleet ? "On" : "Paused"} onChange={v => (v ? setFleet(true) : setConfirm(true))} />
        )}
      </div>
      <div className="polling-types">
        <span className="small muted">Endpoint types, for all plants:</span>
        {CATS.map(c => (
          <Switch key={c} on={polling.types[c] !== false} label={CAT_LABEL[c]} disabled={!polling.fleet}
            title={polling.fleet ? `Pause or resume ${CAT_LABEL[c]} for every plant` : "Fleet polling is paused"}
            onChange={v => setType(c, v)} />
        ))}
      </div>
      {err && <div className="error small">{err}</div>}
    </div>
  );
}

const DEFAULT_PATHS: Record<string, string> = {
  rolling: "forecast/rolling", dayahead: "forecast/dayahead", weekahead: "forecast/weekahead",
  fourmonthsahead: "forecast/fourmonthsahead", mmf: "mmf/live",
};
const AUTH_LABEL: Record<string, string> = { none: "None", header: "Header", query: "Query parameter" };

function KeyBadge({ p }: { p: PlantRow }) {
  if (p.auth_method === "none") return <span className="muted small">No key</span>;
  return (
    <span className="small">
      {p.auth_method === "header" ? `Header ${p.auth_param_name}` : `?${p.auth_param_name}=`}{" "}
      <code>{p.has_api_key ? `••••${p.api_key_hint || ""}` : "missing"}</code>
    </span>
  );
}

type TestResult = {
  category: string; url: string; polled: boolean; verdict: "pass" | "warn" | "fail"; reason: string;
  http_status: number | null; latency_ms: number | null; bytes: number | null; tested_at: string; attempt_id: number | null;
  major: string[]; minor: string[];
};
const VERDICT_LABEL = { pass: "Pass", warn: "Pass with warnings", fail: "Fail" };

export async function runTest(mnem: string, category?: string): Promise<TestResult[]> {
  const r = await api(`/admin/plants/${mnem}/test${category ? `?category=${category}` : ""}`, { method: "POST" });
  return r.results;
}

export function Verdict({ r }: { r: TestResult | "running" | undefined }) {
  if (r === undefined) return <span className="muted small">Not tested</span>;
  if (r === "running") return <span className="verdict running"><Spinner />Testing…</span>;
  const all = [...r.major.map(m => ["major", m]), ...r.minor.map(m => ["minor", m])];
  return (
    <span className="verdict-wrap">
      <span className={`verdict ${r.verdict}`}>{VERDICT_LABEL[r.verdict]}</span>
      <span className="small muted">{r.http_status ? `HTTP ${r.http_status}` : "no response"} · {r.latency_ms ?? "—"} ms
        {r.bytes ? ` · ${r.bytes >= 1024 ? Math.round(r.bytes / 1024) + " KB" : r.bytes + " B"}` : ""} · {fmt(r.tested_at).slice(11)}</span>
      <span className="small">{r.reason}{r.attempt_id && r.verdict !== "pass" ? <> · <a href={`#/attempt/${r.attempt_id}`}>see where</a></> : null}</span>
      {all.length > 1 && (
        <details className="small"><summary>{r.major.length} major, {r.minor.length} minor findings</summary>
          <ul className="flags">{all.map(([sev, m], i) => <li key={i}><span className={`sev ${sev}`}>{sev}</span>{m}</li>)}</ul>
        </details>
      )}
    </span>
  );
}

type TestState = Record<string, TestResult | "running">;

function useTests() {
  const [tests, setTests] = useState<TestState>({});
  const [err, setErr] = useState("");
  const test = async (mnem: string, cats: string[], category?: string) => {
    setErr("");
    setTests(t => ({ ...t, ...Object.fromEntries(cats.map(c => [`${mnem}:${c}`, "running" as const])) }));
    try {
      const res = await runTest(mnem, category);
      setTests(t => ({ ...t, ...Object.fromEntries(res.map(r => [`${mnem}:${r.category}`, r])) }));
    } catch (e: any) {
      setErr(`${mnem.toUpperCase()}: ${e.message}`);
      setTests(t => Object.fromEntries(Object.entries(t).filter(([k, v]) => !(k.startsWith(mnem + ":") && v === "running"))));
    }
  };
  return { tests, test, err };
}

function PlantSummary({ p, tests }: { p: PlantRow; tests: TestState }) {
  const rs = p.endpoints.map(e => tests[`${p.mnemonic}:${e.category}`]).filter(Boolean);
  if (!rs.length) return null;
  if (rs.includes("running")) return <span className="verdict running"><Spinner />Testing…</span>;
  const done = rs as TestResult[];
  const n = (v: string) => done.filter(r => r.verdict === v).length;
  return (
    <span className="small actions">
      {n("pass") > 0 && <span className="verdict pass">{n("pass")} pass</span>}{" "}
      {n("warn") > 0 && <span className="verdict warn">{n("warn")} warn</span>}{" "}
      {n("fail") > 0 && <span className="verdict fail">{n("fail")} fail</span>}
    </span>
  );
}

export function PlantsList() {
  const [rows, setRows] = useState<PlantRow[] | null>(null);
  const [q, setQ] = useState("");
  const [deleting, setDeleting] = useState<PlantRow | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const { tests, test, err } = useTests();
  const [polling, setPolling] = useState<Polling | null>(null);
  const [toggleErr, setToggleErr] = useState("");
  const load = () => { api<PlantRow[]>("/plants").then(setRows); api<Polling>("/admin/polling").then(setPolling); };
  const replaceRow = (p: PlantRow) => setRows(rs => rs && rs.map(r => (r.mnemonic === p.mnemonic ? p : r)));
  const togglePlant = async (p: PlantRow, enabled: boolean) => {
    setToggleErr("");
    try { replaceRow(await api(`/admin/plants/${p.mnemonic}/polling`, { method: "PUT", body: { enabled } })); } catch (e: any) { setToggleErr(e.message); }
  };
  const toggleEndpoint = async (p: PlantRow, c: string, enabled: boolean) => {
    setToggleErr("");
    try { replaceRow(await api(`/admin/plants/${p.mnemonic}/endpoints/${c}/polling`, { method: "PUT", body: { enabled } })); } catch (e: any) { setToggleErr(e.message); }
  };
  const onPolling = (pl: Polling) => { setPolling(pl); api<PlantRow[]>("/plants").then(setRows); };
  const testPlant = (p: PlantRow) => { setOpen(o => ({ ...o, [p.mnemonic]: true })); return test(p.mnemonic, p.endpoints.map(e => e.category)); };
  const [testingAll, setTestingAll] = useState(false);
  const testAll = async () => { setTestingAll(true); try { for (const p of shown) await testPlant(p); } finally { setTestingAll(false); } };
  useEffect(() => { load(); }, []);
  if (!rows) return <Skeleton rows={8} />;
  const shown = rows.filter(p => !q || (p.mnemonic + " " + p.name).toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <div className="title-row">
        <h2>Plants</h2>
        <span className="muted small">{rows.length} registered</span>
        <span style={{ flex: 1 }} />
        <input className="search" type="search" placeholder="Search plants" aria-label="Search plants" value={q} onChange={e => setQ(e.target.value)} />
        <button className="ghost" onClick={testAll} disabled={testingAll || !shown.length}>{testingAll ? <><Spinner />Testing…</> : "Test all plants"}</button>
        <a className="button" href="#/admin/plants/new"><Icon name="plus" />Add plant</a>
      </div>
      <p className="muted small">The poller reads this registry and the polling switches on every tick, so changes apply within 30 seconds. An endpoint is polled only when the fleet, its endpoint type, its plant and the endpoint itself are all switched on (and the plant is live). API keys are encrypted in the database and only their last 4 characters are shown. Test calls each endpoint now with the stored key: <b>Pass</b> means HTTP 200 and rev5-compliant, <b>Pass with warnings</b> means usable data with minor deviations, <b>Fail</b> means unreachable, an error status, or data that breaks a major rule.</p>
      {polling && <PollingPanel polling={polling} onChange={onPolling} />}
      {err && <div className="error" role="alert">{err}</div>}
      {toggleErr && <div className="error" role="alert">{toggleErr}</div>}
      <div className="table-wrap"><table>
        <thead><tr><th><span className="sr-only">Expand</span></th><th>Plant</th><th>Polling</th><th>Status</th><th className="num">Capacity</th><th>Endpoints polled</th><th>API key</th><th>Last test</th><th></th></tr></thead>
        <tbody>{!shown.length && <tr><td colSpan={9} className="muted">{q ? `No plants match "${q}".` : "No plants yet. Add one to start polling."}</td></tr>}{shown.map(p => (<Fragment key={p.mnemonic}>
          <tr>
            <td><button className="ghost icon" aria-label={`Endpoints of ${p.mnemonic.toUpperCase()}`} aria-expanded={!!open[p.mnemonic]}
              onClick={() => setOpen(o => ({ ...o, [p.mnemonic]: !o[p.mnemonic] }))}><Icon name="chevron" className="chev" /></button></td>
            <td><a href={`#/plant/${p.mnemonic}`} className="mnem">{p.mnemonic.toUpperCase()}</a><div className="small muted">{p.name}</div></td>
            <td><Switch on={p.polling_enabled} onChange={v => togglePlant(p, v)} title={`Polling for ${p.mnemonic.toUpperCase()}: pause or resume all its endpoints`} /></td>
            <td><span className={`badge ${p.status === "live" ? "green" : p.status === "onboarding" ? "amber" : ""}`}>{p.status}</span></td>
            <td className="num">{p.capacity_mw ? `${p.capacity_mw} MW` : "—"}</td>
            <td className="small">{CATS.filter(c => p.endpoints.some(e => e.category === c)).map(c => {
              const e = p.endpoints.find(x => x.category === c)!;
              return <span key={c} className={`chip ${e.polled ? "on-soft" : "off"}`} title={e.polled ? `Polled: ${e.url}` : `Not polled: ${e.off_reason}`}>{CAT_LABEL[c]}</span>;
            })}</td>
            <td><KeyBadge p={p} /></td>
            <td><PlantSummary p={p} tests={tests} /></td>
            <td className="actions">
              <button className="ghost" onClick={() => testPlant(p)} disabled={!p.endpoints.length}>Test all</button>
              <a className="button ghost" href={`#/admin/plants/${p.mnemonic}`}>Edit</a>
              <button className="ghost danger" onClick={() => setDeleting(p)}>Delete</button>
            </td>
          </tr>
          {open[p.mnemonic] && (
            <tr className="sub"><td></td><td colSpan={8}>
              {!p.endpoints.length ? <span className="muted small">No endpoints configured.</span> : (
                <table className="inner"><tbody>{CATS.filter(c => p.endpoints.some(e => e.category === c)).map(c => {
                  const e = p.endpoints.find(x => x.category === c)!;
                  return (
                    <tr key={c}>
                      <td className="ep-switch"><Switch on={e.enabled} onChange={v => toggleEndpoint(p, c, v)} title={`Polling for ${p.mnemonic.toUpperCase()} ${CAT_LABEL[c]}`} /></td>
                      <td><b>{CAT_LABEL[c]}</b><div className={`small ${e.polled ? "good" : "muted"}`}>{e.polled ? "Polled on schedule" : e.off_reason}</div></td>
                      <td className="mono small url-cell">{e.url}</td>
                      <td><Verdict r={tests[`${p.mnemonic}:${c}`]} /></td>
                      <td><button className="ghost small-btn" aria-label={`Test ${p.mnemonic.toUpperCase()} ${CAT_LABEL[c]}`} disabled={tests[`${p.mnemonic}:${c}`] === "running"} onClick={() => test(p.mnemonic, [c], c)}>Test</button></td>
                    </tr>
                  );
                })}</tbody></table>
              )}
            </td></tr>
          )}
        </Fragment>))}</tbody>
      </table></div>
      {deleting && <DeleteDialog p={deleting} onClose={() => setDeleting(null)} onDone={() => { setDeleting(null); load(); }} />}
    </>
  );
}

function DeleteDialog({ p, onClose, onDone }: { p: PlantRow; onClose: () => void; onDone: () => void }) {
  const [usage, setUsage] = useState<any>(null);
  const [typed, setTyped] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api(`/admin/plants/${p.mnemonic}/usage`).then(setUsage); }, [p.mnemonic]);
  const busyRef = useRef(false); busyRef.current = busy;
  useEffect(() => {
    // Escape closes the dialog; focus goes back to the button that opened it.
    const prev = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !busyRef.current) onClose(); };
    addEventListener("keydown", onKey);
    return () => { removeEventListener("keydown", onKey); prev?.focus?.(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const del = async () => {
    setBusy(true); setErr("");
    try { await api(`/admin/plants/${p.mnemonic}?confirm=${encodeURIComponent(typed)}`, { method: "DELETE" }); onDone(); return; }
    catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  return (
    <div className="modal" onClick={() => !busy && onClose()}>
      <div className="card" role="alertdialog" aria-modal="true" aria-labelledby="del-title" onClick={e => e.stopPropagation()}>
        <h3 id="del-title">Delete {p.mnemonic.toUpperCase()}?</h3>
        <p>This permanently removes the plant, its endpoints and key, and all of its stored history
          {usage ? <>: <b>{usage.polls}</b> polls, <b>{usage.snapshots}</b> forecast snapshots and <b>{usage.alerts}</b> alerts{usage.users ? <>, and its access for <b>{usage.users}</b> LSS user(s)</> : null}</> : null}.
          Raw responses kept as PPA evidence go too.</p>
        <p className="muted small">To stop polling but keep the history, edit the plant and set its status to <b>suspended</b> instead.</p>
        <label className="field">Type <b>{p.mnemonic.toUpperCase()}</b> to confirm
          <input value={typed} onChange={e => setTyped(e.target.value)} autoFocus /></label>
        {err && <div className="error" role="alert">{err}</div>}
        <div className="actions">
          <button className="ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="danger-fill push-right" disabled={busy || typed.toLowerCase() !== p.mnemonic} onClick={del}>{busy ? <><Spinner />Deleting…</> : "Delete plant and history"}</button>
        </div>
      </div>
    </div>
  );
}

const blankEndpoints = (): Endpoint[] => CATS.map(c => ({ category: c, url: "", enabled: true, note: null }));

export function PlantForm({ mnem }: { mnem?: string }) {
  const editing = !!mnem;
  const [f, setF] = useState<any>(null);
  const [base, setBase] = useState("");
  const [key, setKey] = useState("");
  const [removeKey, setRemoveKey] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const { tests, test, err: testErr } = useTests();

  useEffect(() => {
    if (!editing) {
      setF({ mnemonic: "", name: "", capacity_mw: "", mmf_station_count: "", status: "onboarding", notes: "",
        auth_method: "none", auth_param_name: "", has_api_key: false, api_key_hint: null, endpoints: blankEndpoints() });
      return;
    }
    api<PlantRow>(`/plants/${mnem}`).then(p => {
      const eps = blankEndpoints().map(b => {
        const e = p.endpoints.find(x => x.category === b.category);
        return e ? { ...e } : { ...b, enabled: false };
      });
      setF({ ...p, capacity_mw: p.capacity_mw ?? "", mmf_station_count: p.mmf_station_count ?? "", notes: p.notes ?? "",
        auth_param_name: p.auth_param_name ?? "", endpoints: eps });
    }).catch(e => setErr(e.message));
  }, [mnem]);

  if (!f) return err ? <div className="error" role="alert">{err}</div> : <Skeleton rows={6} />;
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const setEp = (i: number, k: string, v: any) => set("endpoints", f.endpoints.map((e: Endpoint, j: number) => (j === i ? { ...e, [k]: v } : e)));
  const fillFromBase = () => {
    const b = base.trim().replace(/\/+$/, "");
    const m = f.mnemonic.trim().toLowerCase();
    if (!b || !m) { setErr("Enter the mnemonic and a base URL first"); return; }
    setErr("");
    set("endpoints", f.endpoints.map((e: Endpoint) => ({ ...e, url: `${b}/${m}/${DEFAULT_PATHS[e.category]}` })));
  };

  const save = async () => {
    setErr(""); setSaving(true);
    const body: any = {
      mnemonic: f.mnemonic.trim(), name: f.name.trim(), status: f.status, notes: f.notes || null,
      capacity_mw: f.capacity_mw === "" ? null : Number(f.capacity_mw),
      mmf_station_count: f.mmf_station_count === "" ? null : Number(f.mmf_station_count),
      auth_method: f.auth_method, auth_param_name: f.auth_method === "none" ? null : (f.auth_param_name.trim() || null),
      endpoints: f.endpoints.filter((e: Endpoint) => e.url.trim()).map((e: Endpoint) => ({ ...e, url: e.url.trim() })),
    };
    if (f.auth_method === "none") body.api_key = f.has_api_key ? "" : null;
    else if (removeKey) body.api_key = "";
    else if (key) body.api_key = key;
    try {
      const saved = await api(editing ? `/admin/plants/${mnem}` : "/admin/plants", { method: editing ? "PUT" : "POST", body });
      location.hash = editing ? "#/admin/plants" : `#/admin/plants/${saved.mnemonic}`;
      if (!editing) return;
    } catch (e: any) { setErr(e.message); }
    setSaving(false);
  };

  const savedCats: string[] = editing ? f.endpoints.filter((e: Endpoint) => e.url.trim()).map((e: Endpoint) => e.category) : [];
  const testing = savedCats.some(c => tests[`${mnem}:${c}`] === "running");

  const keyPlaceholder = f.has_api_key ? `Leave blank to keep the stored key (••••${f.api_key_hint || ""})` : "Paste the plant's API key";
  return (
    <>
      <div className="title-row">
        <h2 className="crumbs"><a href="#/admin/plants">Plants</a><span className="sep" aria-hidden="true">/</span>{editing ? mnem!.toUpperCase() : "New plant"}</h2>
      </div>
      <div className="form-grid">
        <section className="card">
          <h3>Plant</h3>
          <div className="row2">
            <label className="field">Mnemonic
              <input value={editing ? f.mnemonic.toUpperCase() : f.mnemonic} disabled={editing} maxLength={8} placeholder="e.g. KMLS"
                onChange={e => set("mnemonic", e.target.value.replace(/[^A-Za-z0-9]/g, ""))} /></label>
            <label className="field">Name<input value={f.name} onChange={e => set("name", e.target.value)} placeholder="Plant name" /></label>
          </div>
          <div className="row3">
            <label className="field">Capacity (MW)<input type="number" min={0} step="0.1" value={f.capacity_mw} onChange={e => set("capacity_mw", e.target.value)} /></label>
            <label className="field">MMF stations<input type="number" min={0} value={f.mmf_station_count} onChange={e => set("mmf_station_count", e.target.value)} /></label>
            <label className="field">Status
              <select value={f.status} onChange={e => set("status", e.target.value)}>
                <option value="onboarding">Onboarding (not polled)</option><option value="live">Live (polled)</option><option value="suspended">Suspended (not polled)</option>
              </select></label>
          </div>
          <label className="field">Notes<input value={f.notes} onChange={e => set("notes", e.target.value)} /></label>
        </section>

        <section className="card">
          <h3>API key <span className="muted small">(optional)</span></h3>
          <div className="row2">
            <label className="field">Send the key as
              <select value={f.auth_method} onChange={e => set("auth_method", e.target.value)}>
                {Object.entries(AUTH_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select></label>
            {f.auth_method !== "none" && (
              <label className="field">{f.auth_method === "header" ? "Header name" : "Parameter name"}
                <input value={f.auth_param_name} placeholder={f.auth_method === "header" ? "X-API-Key" : "apiKey"}
                  onChange={e => set("auth_param_name", e.target.value)} /></label>
            )}
          </div>
          {f.auth_method !== "none" && (
            <>
              <label className="field">Key
                <div className="inline">
                  <input type={showKey ? "text" : "password"} value={key} autoComplete="new-password" disabled={removeKey}
                    placeholder={keyPlaceholder} onChange={e => setKey(e.target.value)} />
                  <button type="button" className="ghost" aria-pressed={showKey} aria-label={showKey ? "Hide key" : "Show key"} onClick={() => setShowKey(!showKey)}>{showKey ? "Hide" : "Show"}</button>
                </div></label>
              {f.has_api_key && (
                <label className="toggle"><input type="checkbox" checked={removeKey} onChange={e => setRemoveKey(e.target.checked)} /> Remove the stored key</label>
              )}
              <p className="muted small">Stored encrypted. It is sent on every call to this plant
                {f.auth_method === "header" ? ` as the ${f.auth_param_name || "X-API-Key"} header` : ` as ?${f.auth_param_name || "apiKey"}=…`}, and never shown again after saving.
                {f.auth_method === "query" && " A header is safer: query strings can end up in the plant's server logs."}</p>
            </>
          )}
          {f.auth_method === "none" && f.has_api_key && <p className="muted small">Saving with None removes the stored key.</p>}
        </section>

        <section className="card wide">
          <h3>Endpoints</h3>
          <div className="inline base">
            <input aria-label="Base URL" placeholder="Base URL, e.g. https://ws3.gedine.work" value={base} onChange={e => setBase(e.target.value)} />
            <button type="button" className="ghost" onClick={fillFromBase}>Fill all from base URL</button>
          </div>
          <p className="muted small">Each endpoint has its own full URL, because plants don't all follow the rev5 paths. Leave a URL empty if the plant doesn't serve it. Untick Poll to keep the URL but skip it.</p>
          <div className="table-wrap"><table>
            <thead><tr><th>Endpoint</th><th>URL</th><th>Poll</th><th>Note</th>{editing && <th>Test (saved settings)</th>}</tr></thead>
            <tbody>{f.endpoints.map((e: Endpoint, i: number) => (
              <tr key={e.category}>
                <td><b>{CAT_LABEL[e.category]}</b></td>
                <td><input className="url" aria-label={`${CAT_LABEL[e.category]} URL`} value={e.url} placeholder={`https://…/${f.mnemonic.toLowerCase() || "mnem"}/${DEFAULT_PATHS[e.category]}`}
                  onChange={x => setEp(i, "url", x.target.value)} /></td>
                <td><input type="checkbox" aria-label={`Poll ${CAT_LABEL[e.category]}`} checked={e.enabled} onChange={x => setEp(i, "enabled", x.target.checked)} /></td>
                <td><input aria-label={`${CAT_LABEL[e.category]} note`} value={e.note ?? ""} onChange={x => setEp(i, "note", x.target.value || null)} /></td>
                {editing && <td className="test-cell">{e.url.trim() ? (
                  <div className="inline top">
                    <button className="ghost small-btn" disabled={tests[`${mnem}:${e.category}`] === "running"} onClick={() => test(mnem!, [e.category], e.category)}>Test</button>
                    <Verdict r={tests[`${mnem}:${e.category}`]} />
                  </div>) : <span className="muted small">—</span>}</td>}
              </tr>
            ))}</tbody>
          </table></div>
        </section>
      </div>
      {err && <div className="error" role="alert">{err}</div>}
      <div className="actions bar">
        <button onClick={save} disabled={saving}>{saving ? <><Spinner />Saving…</> : editing ? "Save changes" : "Add plant"}</button>
        {editing && <button className="ghost" onClick={() => test(mnem!, savedCats)} disabled={testing || !savedCats.length}>{testing ? <><Spinner />Testing…</> : "Test all endpoints"}</button>}
        <a className="button ghost" href="#/admin/plants">Cancel</a>
      </div>
      {editing && <p className="muted small">Tests use the saved URL and key, so save first if you changed anything.</p>}
      {testErr && <div className="error" role="alert">{testErr}</div>}
    </>
  );
}
