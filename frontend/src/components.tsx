import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import Icon from "./Icon";

/* Building blocks for a fleet of hundreds of plants: a cached plant index, a searchable multi-select
   plant picker (with region shortcuts), a pager for server-paged lists and a bulk-action bar. */

export type PlantBrief = { mnemonic: string; name: string; region: string | null; status: string };
export const NO_REGION = "No region";

let cache: Promise<PlantBrief[]> | null = null;
/** Every plant the user may see (mnemonic, name, region, status), fetched once and shared by every picker. */
export function loadPlantIndex(fresh = false): Promise<PlantBrief[]> {
  if (!cache || fresh) cache = api<PlantBrief[]>("/plants?brief=true").catch(e => { cache = null; throw e; });
  return cache;
}
/** Call after adding, importing, editing or deleting plants, so pickers show the change. */
export const invalidatePlantIndex = () => { cache = null; };

export function usePlantIndex() {
  const [plants, setPlants] = useState<PlantBrief[] | null>(null);
  useEffect(() => { loadPlantIndex().then(setPlants).catch(() => setPlants([])); }, []);
  return plants;
}

export function regionsOf(plants: PlantBrief[]): { region: string; n: number }[] {
  const m = new Map<string, number>();
  plants.forEach(p => m.set(p.region || NO_REGION, (m.get(p.region || NO_REGION) || 0) + 1));
  return [...m.entries()].map(([region, n]) => ({ region, n }))
    .sort((a, b) => Number(a.region === NO_REGION) - Number(b.region === NO_REGION) || a.region.localeCompare(b.region));
}

export function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

/** Short text for a selection of plants: "Whole fleet", "BDWS", "BDWS, SIDS, KMLS +4". */
export function plantsLabel(sel: string[], allLabel = "Whole fleet", total?: number) {
  if (!sel.length) return allLabel;
  if (total && sel.length === total) return `All ${total} plants`;
  const head = sel.slice(0, 3).map(m => m.toUpperCase()).join(", ");
  return sel.length > 3 ? `${head} +${sel.length - 3}` : head;
}

/** Searchable multi-select of plants. An empty selection means every plant ("Whole fleet").
    single: pick one plant (or the whole fleet) and close. */
export function PlantPicker({ value, onChange, allLabel = "Whole fleet", single = false, disabled = false, label = "Plants", allowAll = true }: {
  value: string[]; onChange: (v: string[]) => void; allLabel?: string; single?: boolean; disabled?: boolean; label?: string; allowAll?: boolean;
}) {
  const plants = usePlantIndex();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [region, setRegion] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  // The list is positioned against the window, so it is never clipped by a scrolling table around the picker.
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  useEffect(() => {
    if (!open) return;
    const place = () => {
      const r = btn.current?.getBoundingClientRect();
      if (!r) return;
      const w = Math.min(440, innerWidth * 0.92);
      setPos({ top: r.bottom + 4, left: Math.max(8, Math.min(r.left, innerWidth - w - 8)) });
    };
    place();
    addEventListener("resize", place); addEventListener("scroll", place, true);
    return () => { removeEventListener("resize", place); removeEventListener("scroll", place, true); };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); btn.current?.focus(); } };
    addEventListener("mousedown", onDown); addEventListener("keydown", onKey);
    return () => { removeEventListener("mousedown", onDown); removeEventListener("keydown", onKey); };
  }, [open]);

  const regions = useMemo(() => regionsOf(plants || []), [plants]);
  const shown = useMemo(() => {
    const n = q.trim().toLowerCase();
    return (plants || []).filter(p => (!region || (p.region || NO_REGION) === region)
      && (!n || p.mnemonic.includes(n) || p.name.toLowerCase().includes(n) || (p.region || "").toLowerCase().includes(n)));
  }, [plants, q, region]);
  const sel = new Set(value);
  const toggle = (m: string) => {
    if (single) { onChange([m]); setOpen(false); btn.current?.focus(); return; }
    onChange(sel.has(m) ? value.filter(x => x !== m) : [...value, m]);
  };
  const addShown = () => onChange([...new Set([...value, ...shown.map(p => p.mnemonic)])]);
  const removeShown = () => { const s = new Set(shown.map(p => p.mnemonic)); onChange(value.filter(m => !s.has(m))); };
  const allShownOn = shown.length > 0 && shown.every(p => sel.has(p.mnemonic));
  const LIMIT = 300;  // rows rendered at once; search narrows the rest

  return (
    <div className="picker" ref={box}>
      <button ref={btn} type="button" className="ghost picker-btn" disabled={disabled} aria-haspopup="dialog" aria-expanded={open}
        aria-label={`${label}: ${plantsLabel(value, allLabel)}`} onClick={() => setOpen(!open)}>
        <span className="picker-val">{plantsLabel(value, allLabel, plants?.length)}</span>
        {value.length > 1 && <span className="badge">{value.length}</span>}
        <Icon name="chevron" size={14} className="chev-down" />
      </button>
      {open && (
        <div className="picker-pop card" style={pos ? { top: pos.top, left: pos.left } : undefined} role="dialog" aria-label={`Choose ${label.toLowerCase()}`}>
          <div className="picker-head">
            <input type="search" className="search" autoFocus placeholder="Search mnemonic, name or region" aria-label="Search plants"
              value={q} onChange={e => setQ(e.target.value)} />
            {regions.length > 1 && (
              <select aria-label="Region" value={region} onChange={e => setRegion(e.target.value)}>
                <option value="">All regions</option>
                {regions.map(r => <option key={r.region} value={r.region}>{r.region} ({r.n})</option>)}
              </select>
            )}
          </div>
          {!single && (
            <div className="picker-actions small">
              {allowAll && <button type="button" className={`ghost small-btn ${value.length ? "" : "on"}`} onClick={() => onChange([])}>{allLabel}</button>}
              <button type="button" className="ghost small-btn" disabled={!shown.length} onClick={allShownOn ? removeShown : addShown}>
                {allShownOn ? "Unselect" : "Select"} {q || region ? "these" : "all"} {shown.length}</button>
              {value.length > 0 && <button type="button" className="ghost small-btn" onClick={() => onChange([])}>Clear ({value.length})</button>}
              <span className="muted push-right">{value.length ? `${value.length} selected` : allowAll ? allLabel : "None selected"}</span>
            </div>
          )}
          <ul className="picker-list" role="listbox" aria-multiselectable={!single}>
            {single && allowAll && (
              <li role="option" aria-selected={!value.length}>
                <label className={!value.length ? "on" : ""}><input type="radio" checked={!value.length} onChange={() => { onChange([]); setOpen(false); }} /><b>{allLabel}</b></label>
              </li>
            )}
            {plants === null && <li className="muted small">Loading plants…</li>}
            {plants && !shown.length && <li className="muted small">No plants match.</li>}
            {shown.slice(0, LIMIT).map(p => (
              <li key={p.mnemonic} role="option" aria-selected={sel.has(p.mnemonic)}>
                <label className={sel.has(p.mnemonic) ? "on" : ""}>
                  <input type={single ? "radio" : "checkbox"} checked={sel.has(p.mnemonic)} onChange={() => toggle(p.mnemonic)} />
                  <span className="mnem">{p.mnemonic.toUpperCase()}</span>
                  <span className="picker-name">{p.name.toLowerCase() !== p.mnemonic ? p.name : ""}</span>
                  <span className="muted small">{p.region || ""}{p.status !== "live" ? ` · ${p.status}` : ""}</span>
                </label>
              </li>
            ))}
            {shown.length > LIMIT && <li className="muted small">{shown.length - LIMIT} more: type to narrow the list.</li>}
          </ul>
          {!single && <div className="picker-foot"><button type="button" onClick={() => { setOpen(false); btn.current?.focus(); }}>Done</button></div>}
        </div>
      )}
    </div>
  );
}

export const PAGE_SIZES = [25, 50, 100, 250];

/** "Rows 1–50 of 1,234", page size and First / Previous / Next / Last, for server-paged lists. */
export function Pager({ total, offset, limit, onOffset, onLimit, noun = "rows", sizes = PAGE_SIZES, allowAll = false }: {
  total: number; offset: number; limit: number; onOffset: (o: number) => void; onLimit?: (n: number) => void;
  noun?: string; sizes?: number[]; allowAll?: boolean;
}) {
  const size = limit || total || 1;
  const last = limit ? Math.min(offset + limit, total) : total;
  if (!total) return null;
  return (
    <div className="pager">
      <span className="muted small">{noun[0].toUpperCase() + noun.slice(1)} {(offset + 1).toLocaleString()}–{last.toLocaleString()} of {total.toLocaleString()}</span>
      {onLimit && (
        <label className="small muted">Per page{" "}
          <select value={limit} onChange={e => { onLimit(Number(e.target.value)); onOffset(0); }}>
            {sizes.map(n => <option key={n} value={n}>{n}</option>)}
            {allowAll && <option value={0}>All</option>}
          </select>
        </label>
      )}
      {limit > 0 && total > limit && <>
        <button className="ghost small-btn" disabled={offset === 0} onClick={() => onOffset(0)}>First</button>
        <button className="ghost small-btn" disabled={offset === 0} onClick={() => onOffset(Math.max(0, offset - size))}>Previous</button>
        <span className="small muted">Page {Math.floor(offset / size) + 1} of {Math.ceil(total / size)}</span>
        <button className="ghost small-btn" disabled={last >= total} onClick={() => onOffset(offset + size)}>Next</button>
        <button className="ghost small-btn" disabled={last >= total} onClick={() => onOffset(Math.floor((total - 1) / size) * size)}>Last</button>
      </>}
    </div>
  );
}

/** Sticky bar shown while rows are selected: count, select-all-matching, clear, and the actions. */
export function BulkBar({ count, matched, onSelectAll, onClear, children, noun = "plant" }: {
  count: number; matched?: number; onSelectAll?: () => void; onClear: () => void; children: React.ReactNode; noun?: string;
}) {
  if (!count) return null;
  return (
    <div className="bulk-bar" role="region" aria-label="Actions on selected">
      <b>{count} {noun}{count === 1 ? "" : "s"} selected</b>
      {onSelectAll && matched !== undefined && matched > count && (
        <button className="ghost small-btn" onClick={onSelectAll}>Select all {matched} matching</button>
      )}
      <button className="ghost small-btn" onClick={onClear}>Clear</button>
      <span className="bulk-actions">{children}</span>
    </div>
  );
}

/** Tri-state "select every row on this page" checkbox. */
export function PageCheck({ ids, sel, onChange, label = "Select all on this page" }: {
  ids: string[]; sel: Set<string>; onChange: (next: Set<string>) => void; label?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const on = ids.filter(i => sel.has(i)).length;
  useEffect(() => { if (ref.current) ref.current.indeterminate = on > 0 && on < ids.length; }, [on, ids.length]);
  return <input ref={ref} type="checkbox" aria-label={label} checked={ids.length > 0 && on === ids.length} disabled={!ids.length}
    onChange={e => { const n = new Set(sel); ids.forEach(i => (e.target.checked ? n.add(i) : n.delete(i))); onChange(n); }} />;
}

/** Read and write a page's filters in the address bar (#/page?a=1&b=2) without adding history entries. */
export function useQueryState<T extends Record<string, string>>(page: string, query: string, defaults: T) {
  const [state, setState] = useState<T>(() => {
    const p = new URLSearchParams(query);
    const out: Record<string, string> = { ...defaults };
    Object.keys(defaults).forEach(k => { const v = p.get(k); if (v !== null) out[k] = v; });
    return out as T;
  });
  useEffect(() => {
    const p = new URLSearchParams();
    Object.entries(state).forEach(([k, v]) => { if (v !== defaults[k]) p.set(k, v); });
    const qs = p.toString();
    history.replaceState(null, "", `#${page}${qs ? "?" + qs : ""}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);
  const set = (patch: Partial<T>) => setState(s => ({ ...s, ...patch }));
  return [state, set] as const;
}
