import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Icon from "./Icon";

/* A spreadsheet-style grid for report data: column letters and row numbers, gridlines, a frozen header
   and first column, resizable columns (drag the header edge, double-click to fit), a filter box under
   every header, and cell selection like a worksheet (click, drag, Shift+click, arrow keys, Ctrl+A).
   Ctrl+C copies the selection as tab-separated values, which pastes straight into Excel or Sheets.
   The name box and formula bar show the active cell, and the status bar sums the selected numbers.
   Sorting and filtering run on the server, so they cover every row, not only the page on screen. */

export type SheetCol = { key: string; label: string; type: "text" | "num" | "dt" | "bool" | "int"; hint?: string | null };
type Pos = { r: number; c: number };
type Sel = { a: Pos; b: Pos };

const NUMERIC = new Set(["num", "int"]);
const ROWNUM_W = 56;
const letter = (i: number) => { let s = ""; i += 1; while (i > 0) { const m = (i - 1) % 26; s = String.fromCharCode(65 + m) + s; i = Math.floor((i - 1) / 26); } return s; };
const labelWidth = (c: SheetCol) => Math.min(240, c.label.length * 7.6 + 34);
const defaultWidth = (c: SheetCol) => Math.round(Math.max(labelWidth(c),
  c.type === "dt" ? 158 : c.type === "num" ? 96 : c.type === "int" ? 80 : c.type === "bool" ? 90 : 110));
const HINT: Record<string, string> = {
  num: "e.g. >0, <=5, 1..10", int: "e.g. 5, >100", dt: "e.g. 2026-11-28 or >…", bool: "yes / no", text: "contains…",
};
const HELP = "Filter: text = contains (=exact, !not). Numbers: 5, >5, <=5, 1..10. Dates: 2026-11-28 05:30 (contains), >2026-11-28 05:00, a..b. blank / !blank. Applies to all rows and to the CSV.";

/** Text of a cell as shown and as copied. */
export function cellText(v: any, type: string): string {
  if (v === null || v === undefined) return "";
  if (type === "bool") return v ? "Yes" : "No";
  if (type === "dt" && typeof v === "string") return v.replace("T", " ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function copyText(text: string) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  // Plain-http deployments have no clipboard API: fall back to a hidden textarea.
  const t = document.createElement("textarea");
  t.value = text; t.style.position = "fixed"; t.style.opacity = "0";
  document.body.appendChild(t); t.select();
  try { document.execCommand("copy"); } finally { t.remove(); }
  return Promise.resolve();
}

const fmtNum = (n: number) => (Math.abs(n) >= 1e6 || Number.isInteger(n) ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: 4 }));

export default function Sheet({ columns, rows, offset, storageKey, sort = null, onSort, filters = {}, onFilter, loading, display, footer, cellLink, frozen = 1, freezeLabel }: {
  columns: SheetCol[]; rows: Record<string, any>[]; offset: number; storageKey: string;
  sort?: { key: string; desc: boolean } | null; onSort?: (key: string) => void;
  filters?: Record<string, string>; onFilter?: (key: string, value: string) => void;
  frozen?: number; freezeLabel?: string;
  loading?: boolean; display?: (col: SheetCol, v: any) => string; footer?: ReactNode;
  cellLink?: (row: Record<string, any>, col: SheetCol) => { href: string; label: string } | null;
}) {
  const show = display || ((c: SheetCol, v: any) => cellText(v, c.type));
  const [widths, setWidths] = useState<Record<string, number>>(() => {
    try { return JSON.parse(localStorage.getItem(`gso_colw_${storageKey}`) || "{}"); } catch { return {}; }
  });
  useEffect(() => {
    try { setWidths(JSON.parse(localStorage.getItem(`gso_colw_${storageKey}`) || "{}")); } catch { setWidths({}); }
  }, [storageKey]);
  const saveWidths = (w: Record<string, number>) => { try { localStorage.setItem(`gso_colw_${storageKey}`, JSON.stringify(w)); } catch { /* ignore */ } };
  const width = (c: SheetCol) => widths[c.key] ?? defaultWidth(c);

  const [freeze, setFreeze] = useState(true);
  const [full, setFull] = useState(false);
  const [sel, setSel] = useState<Sel | null>(null);
  const [copied, setCopied] = useState("");
  const dragging = useRef(false);
  const box = useRef<HTMLDivElement>(null);
  const nRows = rows.length, nCols = columns.length;

  // A new page or new columns: start the selection again at the top-left cell.
  useEffect(() => { setSel(nRows && nCols ? { a: { r: 0, c: 0 }, b: { r: 0, c: 0 } } : null); }, [rows, columns, nRows, nCols]);
  useEffect(() => {
    const up = () => { dragging.current = false; };
    addEventListener("mouseup", up); return () => removeEventListener("mouseup", up);
  }, []);
  useEffect(() => {
    if (!full) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setFull(false); };
    addEventListener("keydown", esc); return () => removeEventListener("keydown", esc);
  }, [full]);

  const rect = sel && {
    r0: Math.min(sel.a.r, sel.b.r), r1: Math.max(sel.a.r, sel.b.r),
    c0: Math.min(sel.a.c, sel.b.c), c1: Math.max(sel.a.c, sel.b.c),
  };
  const inSel = (r: number, c: number) => !!rect && r >= rect.r0 && r <= rect.r1 && c >= rect.c0 && c <= rect.c1;

  const clamp = (p: Pos): Pos => ({ r: Math.max(0, Math.min(nRows - 1, p.r)), c: Math.max(0, Math.min(nCols - 1, p.c)) });
  const scrollTo = (p: Pos) => {
    const el = box.current?.querySelector<HTMLElement>(`td[data-rc="${p.r}:${p.c}"]`);
    el?.scrollIntoView({ block: "nearest", inline: "nearest" });
  };

  const selectionText = useCallback((withHeader: boolean) => {
    if (!rect) return "";
    const cols = columns.slice(rect.c0, rect.c1 + 1);
    const lines = rows.slice(rect.r0, rect.r1 + 1).map(r => cols.map(c => cellText(r[c.key], c.type).replace(/[\t\n]/g, " ")).join("\t"));
    return (withHeader ? [cols.map(c => c.label).join("\t"), ...lines] : lines).join("\n");
  }, [rect, rows, columns]);

  const doCopy = (withHeader: boolean, what: string) => {
    copyText(selectionText(withHeader)).then(() => { setCopied(what); setTimeout(() => setCopied(""), 1800); });
  };

  const onKey = (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).tagName === "INPUT" || !sel) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "c") { e.preventDefault(); doCopy(false, "Copied"); return; }
    if (mod && e.key.toLowerCase() === "a") { e.preventDefault(); setSel({ a: { r: 0, c: 0 }, b: { r: nRows - 1, c: nCols - 1 } }); return; }
    const moves: Record<string, Pos> = {
      ArrowUp: { r: -1, c: 0 }, ArrowDown: { r: 1, c: 0 }, ArrowLeft: { r: 0, c: -1 }, ArrowRight: { r: 0, c: 1 },
      Enter: { r: e.shiftKey ? -1 : 1, c: 0 }, Tab: { r: 0, c: e.shiftKey ? -1 : 1 },
      PageDown: { r: 20, c: 0 }, PageUp: { r: -20, c: 0 },
    };
    let to: Pos | null = null;
    const from = e.shiftKey && !["Enter", "Tab"].includes(e.key) ? sel.b : sel.a;
    if (moves[e.key]) {
      const m = moves[e.key];
      to = mod ? clamp({ r: m.r ? (m.r > 0 ? nRows - 1 : 0) : from.r, c: m.c ? (m.c > 0 ? nCols - 1 : 0) : from.c })
        : clamp({ r: from.r + m.r, c: from.c + m.c });
    } else if (e.key === "Home") to = clamp({ r: mod ? 0 : from.r, c: 0 });
    else if (e.key === "End") to = clamp({ r: mod ? nRows - 1 : from.r, c: nCols - 1 });
    if (!to) return;
    e.preventDefault();
    if (e.shiftKey && !["Enter", "Tab"].includes(e.key)) setSel({ a: sel.a, b: to });
    else setSel({ a: to, b: to });
    scrollTo(to);
  };

  const startResize = (e: React.MouseEvent, c: SheetCol) => {
    e.preventDefault(); e.stopPropagation();
    const x0 = e.clientX, w0 = width(c);
    let latest = widths;
    const move = (ev: MouseEvent) => { latest = { ...latest, [c.key]: Math.max(48, Math.round(w0 + ev.clientX - x0)) }; setWidths(latest); };
    const up = () => { removeEventListener("mousemove", move); removeEventListener("mouseup", up); saveWidths(latest); document.body.classList.remove("col-resizing"); };
    addEventListener("mousemove", move); addEventListener("mouseup", up);
    document.body.classList.add("col-resizing");
  };
  const autoFit = (c: SheetCol) => {
    const longest = Math.max(c.label.length + 2, ...rows.slice(0, 500).map(r => cellText(r[c.key], c.type).length));
    const w = { ...widths, [c.key]: Math.min(520, Math.max(56, Math.round(longest * 7.4 + 26))) };
    setWidths(w); saveWidths(w);
  };
  const resetWidths = () => { setWidths({}); saveWidths({}); };

  // Status bar: like Excel, count the selected cells and sum the numeric ones.
  const stats = useMemo(() => {
    if (!rect) return null;
    let count = 0, n = 0, sum = 0, min = Infinity, max = -Infinity;
    for (let r = rect.r0; r <= rect.r1; r++) for (let c = rect.c0; c <= rect.c1; c++) {
      const col = columns[c]; const v = rows[r]?.[col.key];
      if (v === null || v === undefined || v === "") continue;
      count++;
      const x = typeof v === "number" ? v : NUMERIC.has(col.type) ? Number(v) : NaN;
      if (!Number.isNaN(x) && typeof v !== "boolean") { n++; sum += x; min = Math.min(min, x); max = Math.max(max, x); }
    }
    return { cells: (rect.r1 - rect.r0 + 1) * (rect.c1 - rect.c0 + 1), count, n, sum, min, max };
  }, [rect, rows, columns]);

  const active = sel ? sel.a : null;
  const activeCol = active ? columns[active.c] : null;
  const activeRow = active ? rows[active.r] : null;
  const activeVal = activeCol && activeRow ? cellText(activeRow[activeCol.key], activeCol.type) : "";
  const link = activeCol && activeRow && cellLink ? cellLink(activeRow, activeCol) : null;
  // Frozen columns stay in view while scrolling sideways (like Excel's freeze panes).
  const nFrozen = freeze ? Math.min(frozen, columns.length) : 0;
  const lefts: number[] = [];
  columns.slice(0, nFrozen).reduce((x, c, i) => { lefts[i] = x; return x + width(c); }, ROWNUM_W);
  const frz = (i: number) => (i < nFrozen ? `frz${i === nFrozen - 1 ? " frz-last" : ""}` : "");
  const fst = (i: number) => (i < nFrozen ? { left: lefts[i] } : undefined);
  const totalW = ROWNUM_W + columns.reduce((s, c) => s + width(c), 0);

  return (
    <div className={`xl ${full ? "full" : ""}`}>
      <div className="xl-bar">
        <span className="xl-name" aria-label="Active cell">{active ? `${letter(active.c)}${offset + active.r + 1}` : ""}</span>
        <span className="xl-fx" aria-hidden="true">fx</span>
        <input className="xl-formula" readOnly value={activeVal} aria-label="Active cell value" onFocus={e => e.target.select()} />
        <span className="xl-tools">
          <label className="check-field small"><input type="checkbox" checked={freeze} onChange={e => setFreeze(e.target.checked)} />{freezeLabel || (frozen > 1 ? `Freeze first ${frozen} columns` : "Freeze first column")}</label>
          <button className="ghost small-btn" onClick={() => doCopy(true, "Copied with headers")} disabled={!sel} title="Copy the selected cells with their column headers (Ctrl+C copies without)">Copy</button>
          <button className="ghost small-btn" onClick={resetWidths} title="Reset column widths">Reset widths</button>
          {onFilter && Object.values(filters).some(Boolean) && <button className="ghost small-btn" onClick={() => columns.forEach(c => filters[c.key] && onFilter!(c.key, ""))}>Clear filters</button>}
          <button className="ghost small-btn" onClick={() => setFull(!full)} aria-pressed={full}>{full ? "Exit full screen" : "Full screen"}</button>
        </span>
      </div>

      <div className={`xl-scroll ${loading ? "stale" : ""}`} ref={box} tabIndex={0} onKeyDown={onKey}
        role="grid" aria-label="Report data" aria-rowcount={rows.length} aria-colcount={columns.length}>
        <table style={{ width: totalW }}>
          <colgroup><col style={{ width: ROWNUM_W }} />{columns.map(c => <col key={c.key} style={{ width: width(c) }} />)}</colgroup>
          <thead>
            <tr className="xl-letters">
              <th className="xl-corner" onClick={() => nRows && setSel({ a: { r: 0, c: 0 }, b: { r: nRows - 1, c: nCols - 1 } })} title="Select all (Ctrl+A)" />
              {columns.map((c, i) => (
                <th key={c.key} className={`${rect && i >= rect.c0 && i <= rect.c1 ? "hl" : ""} ${frz(i)}`} style={fst(i)}
                  onClick={() => nRows && setSel({ a: { r: 0, c: i }, b: { r: nRows - 1, c: i } })}>{letter(i)}</th>
              ))}
            </tr>
            <tr className="xl-head">
              <th className="xl-corner" />
              {columns.map((c, i) => {
                const on = sort?.key === c.key;
                return (
                  <th key={c.key} scope="col" className={`${NUMERIC.has(c.type) ? "num" : ""} ${frz(i)}`} style={fst(i)} title={c.hint || undefined}
                    aria-sort={on ? (sort!.desc ? "descending" : "ascending") : "none"}>
                    {onSort ? <button className="sort" onClick={() => onSort(c.key)} title={`${c.label}: click to sort (all rows)`}>
                      <span className="lbl">{c.label}</span><span aria-hidden="true" className="sort-ind">{on ? (sort!.desc ? "▼" : "▲") : ""}</span>
                    </button> : <span className="sort static"><span className="lbl">{c.label}</span></span>}
                    <span className="xl-resize" onMouseDown={e => startResize(e, c)} onDoubleClick={() => autoFit(c)} title="Drag to resize, double-click to fit" />
                  </th>
                );
              })}
            </tr>
            {onFilter && (<tr className="xl-filters">
              <th className="xl-corner" title={HELP}><Icon name="list" size={12} /></th>
              {columns.map((c, i) => (
                <th key={c.key} className={frz(i)} style={fst(i)}>
                  <FilterBox value={filters[c.key] || ""} placeholder={HINT[c.type] || HINT.text} label={`Filter ${c.label}`}
                    onChange={v => onFilter(c.key, v)} />
                </th>
              ))}
            </tr>)}
          </thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={offset + ri}>
                <th scope="row" className={`xl-rownum ${rect && ri >= rect.r0 && ri <= rect.r1 ? "hl" : ""}`}
                  onClick={() => setSel({ a: { r: ri, c: 0 }, b: { r: ri, c: nCols - 1 } })}>{offset + ri + 1}</th>
                {columns.map((c, ci) => {
                  const v = r[c.key];
                  const isActive = active && active.r === ri && active.c === ci;
                  const cls = [NUMERIC.has(c.type) ? "num" : "", inSel(ri, ci) ? "sel" : "", isActive ? "act" : "",
                    frz(ci), c.type === "bool" ? (v ? "yes" : v === false ? "no" : "") : "",
                    v === null || v === undefined || v === "" ? "empty" : ""].join(" ");
                  return (
                    <td key={c.key} data-rc={`${ri}:${ci}`} className={cls} style={fst(ci)}
                      title={show(c, v)}
                      onMouseDown={e => {
                        if (e.button !== 0) return;
                        box.current?.focus({ preventScroll: true });
                        dragging.current = true;
                        setSel(s => (e.shiftKey && s ? { a: s.a, b: { r: ri, c: ci } } : { a: { r: ri, c: ci }, b: { r: ri, c: ci } }));
                      }}
                      onMouseEnter={() => { if (dragging.current) setSel(s => (s ? { a: s.a, b: { r: ri, c: ci } } : s)); }}>
                      {show(c, v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="xl-status" role="status">
        {stats && <>
          {stats.cells > 1 && <span>{stats.cells.toLocaleString()} cells</span>}
          <span>Count: <b>{stats.count.toLocaleString()}</b></span>
          {stats.n > 0 && <>
            <span>Sum: <b>{fmtNum(stats.sum)}</b></span>
            <span>Average: <b>{fmtNum(stats.sum / stats.n)}</b></span>
            {stats.n > 1 && <><span>Min: <b>{fmtNum(stats.min)}</b></span><span>Max: <b>{fmtNum(stats.max)}</b></span></>}
          </>}
        </>}
        {link && <a href={link.href}>{link.label}</a>}
        {copied && <span className="good">{copied}</span>}
        <span className="xl-keys muted">Click or drag to select · Shift+arrows to extend · Ctrl+C to copy into Excel</span>
      </div>
      {footer}
    </div>
  );
}

/** A filter input that waits for a pause in typing before asking the server again. */
function FilterBox({ value, placeholder, label, onChange }: { value: string; placeholder: string; label: string; onChange: (v: string) => void }) {
  const [v, setV] = useState(value);
  const first = useRef(true);
  useEffect(() => { setV(value); }, [value]);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    if (v === value) return;
    const t = setTimeout(() => onChange(v.trim()), 450);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [v]);
  return (
    <span className="xl-filter">
      <input value={v} placeholder={placeholder} aria-label={label} onChange={e => setV(e.target.value)}
        onKeyDown={e => { if (e.key === "Enter") onChange(v.trim()); if (e.key === "Escape") { setV(""); onChange(""); } }} />
      {v && <button className="icon ghost" aria-label={`Clear ${label.toLowerCase()}`} onClick={() => { setV(""); onChange(""); }}><Icon name="x" size={11} /></button>}
    </span>
  );
}
