import { useEffect, useMemo, useRef, useState } from "react";
import { api, CAT_LABEL, fmt } from "../api";

/* Raw response inspector: shows the exact JSON a plant returned, with every place that caused a
   compliance finding highlighted (red = major, amber = minor). Findings are listed on the right;
   clicking one focuses it and steps through its locations. */

type Flag = { id: number; rule: string; severity: "major" | "minor"; message: string; paths: string[]; whole_response: boolean; detail: any };
type Part = { text: string; cls?: string; path?: string };
type Line = { indent: number; parts: Part[]; path: string; close?: boolean };

const ROW = 20;
const esc = (k: string) => k.replace(/~/g, "~0").replace(/\//g, "~1");

function prim(v: unknown): Part {
  if (v === null) return { text: "null", cls: "j-null" };
  if (typeof v === "string") return { text: JSON.stringify(v), cls: "j-str" };
  if (typeof v === "number") return { text: String(v), cls: "j-num" };
  if (typeof v === "boolean") return { text: String(v), cls: "j-bool" };
  return { text: JSON.stringify(v) };
}

const isPrim = (v: unknown) => v === null || typeof v !== "object";

/** Flatten JSON into display lines. Small objects of plain values (one forecast slot, one MMF
 *  parameter) go on a single line so large payloads stay readable. */
function flatten(value: unknown): Line[] {
  const out: Line[] = [];
  const walk = (v: unknown, path: string, indent: number, key: string | null, last: boolean) => {
    const comma = last ? "" : ",";
    const kp: Part[] = key !== null ? [{ text: JSON.stringify(key), cls: "j-key", path }, { text: ": " }] : [];
    if (isPrim(v)) { out.push({ indent, path, parts: [...kp, { ...prim(v), path }, { text: comma }] }); return; }
    const isArr = Array.isArray(v);
    const entries: [string, unknown][] = isArr ? (v as unknown[]).map((x, i) => [String(i), x]) : Object.entries(v as object);
    const [o, c] = isArr ? ["[", "]"] : ["{", "}"];
    if (!entries.length) { out.push({ indent, path, parts: [...kp, { text: o + c + comma }] }); return; }
    if (!isArr && entries.length <= 10 && entries.every(([, x]) => isPrim(x))) {
      const parts: Part[] = [...kp, { text: "{ " }];
      entries.forEach(([k, x], i) => {
        const p = `${path}/${esc(k)}`;
        parts.push({ text: JSON.stringify(k), cls: "j-key", path: p }, { text: ": " }, { ...prim(x), path: p });
        if (i < entries.length - 1) parts.push({ text: ", " });
      });
      parts.push({ text: " }" + comma });
      out.push({ indent, path, parts });
      return;
    }
    out.push({ indent, path, parts: [...kp, { text: o }, ...(isArr ? [{ text: `  ${entries.length} items`, cls: "j-meta" }] : [])] });
    entries.forEach(([k, x], i) => walk(x, `${path}/${esc(k)}`, indent + 1, isArr ? null : k, i === entries.length - 1));
    out.push({ indent, path, close: true, parts: [{ text: c + comma }] });
  };
  walk(value, "", 0, null, true);
  return out;
}

export default function Inspect({ id, embedded = false, canPoll = false }: { id: string; embedded?: boolean; canPoll?: boolean }) {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState("");
  const [active, setActive] = useState<number | null>(null);
  const [pos, setPos] = useState<Record<number, number>>({});
  const [show, setShow] = useState({ major: true, minor: true });
  const [scrollTop, setScrollTop] = useState(0);
  const [flash, setFlash] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(600);

  useEffect(() => { setD(null); api(`/attempts/${id}/inspect`).then(setD).catch(e => setErr(e.message)); }, [id]);
  useEffect(() => {
    const on = () => box.current && setHeight(box.current.clientHeight);
    on(); addEventListener("resize", on); return () => removeEventListener("resize", on);
  }, [d]);

  // Prefer the exact text the plant sent (keeps its key order); fall back to the stored JSONB.
  const parsed = useMemo(() => {
    if (!d?.raw) return null;
    if (d.raw.body_text) { try { return JSON.parse(d.raw.body_text); } catch { return null; } }
    return d.raw.body ?? null;
  }, [d]);
  const lines = useMemo(() => (parsed != null ? flatten(parsed) : []), [parsed]);
  const flags: Flag[] = d?.flags || [];

  // path -> first line index (opening lines and value parts)
  const lineOf = useMemo(() => {
    const m = new Map<string, number>();
    lines.forEach((l, i) => {
      if (!l.close && !m.has(l.path)) m.set(l.path, i);
      l.parts.forEach(p => p.path !== undefined && !m.has(p.path) && m.set(p.path, i));
    });
    return m;
  }, [lines]);

  // path -> flag indices that point at it
  const hits = useMemo(() => {
    const m = new Map<string, number[]>();
    flags.forEach((f, fi) => {
      if (!show[f.severity] || f.whole_response) return;
      f.paths.forEach(p => { const a = m.get(p); a ? a.push(fi) : m.set(p, [fi]); });
    });
    return m;
  }, [flags, show]);

  const occurrences = useMemo(() => flags.map(f => {
    const idx = f.paths.map(p => lineOf.get(p)).filter((x): x is number => x !== undefined);
    return Array.from(new Set(idx)).sort((a, b) => a - b);
  }), [flags, lineOf]);

  const jump = (fi: number, dir: 0 | 1 | -1) => {
    const occ = occurrences[fi];
    if (!occ.length || !box.current) { setActive(fi); return; }
    const cur = active === fi ? pos[fi] ?? 0 : 0;
    const next = dir === 0 ? (active === fi ? cur : 0) : (cur + dir + occ.length) % occ.length;
    setActive(fi); setPos({ ...pos, [fi]: next });
    const line = occ[next];
    box.current.scrollTop = Math.max(0, line * ROW - box.current.clientHeight / 3);
    setFlash(line);
  };

  if (err) return <div className="error">{err}</div>;
  if (!d) return <p className="muted">Loading…</p>;
  const a = d.attempt;
  const sevOf = (fis: number[]) => (fis.some(i => flags[i].severity === "major") ? "major" : "minor");
  const tip = (fis: number[]) => fis.map(i => `[${flags[i].severity.toUpperCase()}] ${flags[i].rule}: ${flags[i].message}`).join("\n");
  const cls = (fis: number[] | undefined) => {
    if (!fis) return "";
    const focus = active === null || fis.includes(active);
    return `hl ${sevOf(active !== null && focus ? [active] : fis)}${focus ? "" : " faint"}`;
  };

  const first = Math.max(0, Math.floor(scrollTop / ROW) - 30);
  const last = Math.min(lines.length, Math.ceil((scrollTop + height) / ROW) + 30);
  const visible = lines.slice(first, last);
  const majors = flags.filter(f => f.severity === "major").length;
  const status = a.http_status !== 200 ? "down" : a.is_valid ? (a.minor_flags ? "minor" : "ok") : "invalid";
  const STATUS = { ok: "Compliant", minor: "Minor issues", invalid: "Non-compliant", down: "Unreachable" } as Record<string, string>;

  return (
    <>
      {!embedded && <div className="title-row">
        <h2><a href="#/" className="muted">Fleet</a> / <a href={`#/plant/${d.plant.mnemonic}`} className="muted">{d.plant.mnemonic.toUpperCase()}</a> / {CAT_LABEL[a.category]} response</h2>
        <span className="cell-state"><i className={`dot ${status}`} />{STATUS[status]}</span>
        {canPoll && <a className="button ghost poll-link" href={`#/admin/poll/${d.plant.mnemonic}/${a.category}/run`}>Poll this API now</a>}
      </div>}
      <div className="facts">
        <div><span>Polled</span>{fmt(a.started_at)}{a.scheduled_for ? "" : " (manual)"}</div>
        <div><span>HTTP</span>{a.http_status ?? "no response"} · {a.latency_ms} ms</div>
        <div><span>Size</span>{a.bytes ? `${Math.round(a.bytes / 1024)} KB` : "—"}</div>
        <div><span>Findings</span>{majors} major, {flags.length - majors} minor</div>
        {d.raw && <div><span>SHA-256</span><code title={d.raw.sha256}>{d.raw.sha256.slice(0, 16)}…</code></div>}
      </div>
      <p className="muted small url-line"><code>{a.url}</code></p>
      {d.raw && d.raw.first_seen_attempt !== a.id && (
        <p className="muted small">The plant sent exactly the same content as in an earlier poll (first received {fmt(d.raw.received_at)}), so that stored copy is shown.</p>
      )}

      <div className={`inspect${embedded ? " embedded" : ""}`}>
        <div className="json-pane">
          {!d.raw ? (
            <div className="card"><b>No response body stored.</b><p className="muted">{a.error || `HTTP ${a.http_status}`}</p></div>
          ) : parsed == null ? (
            <pre className="json-text hl major">{d.raw.body_text}</pre>
          ) : (
            <div className="json" ref={box} onScroll={e => setScrollTop(e.currentTarget.scrollTop)}>
              <div style={{ height: lines.length * ROW, position: "relative" }}>
                {visible.map((l, k) => {
                  const i = first + k;
                  const lineHits = !l.close ? hits.get(l.path) : undefined;
                  return (
                    <div key={i} className={`jl ${cls(lineHits)} ${flash === i ? "flash" : ""}`} style={{ top: i * ROW }}
                      title={lineHits ? tip(lineHits) : undefined}>
                      <span className="ln">{i + 1}</span>
                      <span style={{ display: "inline-block", width: l.indent * 16 }} />
                      {l.parts.map((p, j) => {
                        const ph = p.path !== undefined && p.path !== l.path ? hits.get(p.path) : undefined;
                        return <span key={j} className={`${p.cls || ""} ${ph ? cls(ph) : ""}`} title={ph ? tip(ph) : undefined}>{p.text}</span>;
                      })}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        <aside className="findings">
          <div className="findings-head">
            <b>Findings</b>
            <label><input type="checkbox" checked={show.major} onChange={e => setShow({ ...show, major: e.target.checked })} /> Major</label>
            <label><input type="checkbox" checked={show.minor} onChange={e => setShow({ ...show, minor: e.target.checked })} /> Minor</label>
            {active !== null && <button className="ghost small-btn" onClick={() => setActive(null)}>Show all</button>}
          </div>
          {!flags.length && <p className="okline">No findings: this response is compliant.</p>}
          {flags.map((f, fi) => {
            const occ = occurrences[fi];
            return (
              <div key={f.id} className={`finding ${f.severity} ${active === fi ? "on" : ""} ${show[f.severity] ? "" : "hidden"}`}
                onClick={() => jump(fi, 0)}>
                <div><span className={`sev ${f.severity}`}>{f.severity}</span><code>{f.rule}</code></div>
                <div className="msg">{f.message}</div>
                {f.whole_response ? <div className="small muted">Applies to the whole response</div> : (
                  <div className="nav small" onClick={e => e.stopPropagation()}>
                    <span className="muted">{f.paths.length === 1 ? "1 place" : `${f.paths.length} places`}{occ.length !== f.paths.length ? ` on ${occ.length} line${occ.length === 1 ? "" : "s"}` : ""}{active === fi && occ.length > 1 ? ` · line ${(pos[fi] ?? 0) + 1} of ${occ.length}` : ""}</span>
                    {occ.length > 1 && <>
                      <button className="ghost small-btn" onClick={() => jump(fi, -1)} aria-label="Previous">‹</button>
                      <button className="ghost small-btn" onClick={() => jump(fi, 1)} aria-label="Next">›</button>
                    </>}
                    {occ.length === 1 && <button className="ghost small-btn" onClick={() => jump(fi, 0)}>Show</button>}
                  </div>
                )}
              </div>
            );
          })}
          <p className="muted small legend-note"><span className="hl major">red</span> major, <span className="hl minor">amber</span> minor. Hover a highlight for the reason.</p>
        </aside>
      </div>
    </>
  );
}
