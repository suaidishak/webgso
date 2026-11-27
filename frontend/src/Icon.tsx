/* Small inline SVG icon set (Lucide-style 24px grid, 2px stroke). Decorative by default:
   every use sits next to visible text, so icons are hidden from screen readers. */
const PATHS: Record<string, string> = {
  grid: "M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z",
  bell: "M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0",
  shield: "M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1zM9 12l2 2 4-4",
  zap: "M13 2 3 14h9l-1 8 10-12h-9l1-8z",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41",
  moon: "M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z",
  users: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75",
  clock: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM12 6v6l4 2",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
  chevron: "m9 18 6-6-6-6",
  alert: "M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01",
  pause: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM10 15V9M14 15V9",
  refresh: "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16M3 21v-5h5M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8M21 3v5h-5",
  plus: "M12 5v14M5 12h14",
  table: "M3 3h18v18H3zM3 9h18M3 15h18M9 3v18",
  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3",
  x: "M18 6 6 18M6 6l12 12",
  help: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3M12 17h.01",
  compass: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM16.24 7.76l-2.12 6.36-6.36 2.12 2.12-6.36z",
  check: "M20 6 9 17l-5-5",
};

export default function Icon({ name, size = 16, className = "" }: { name: keyof typeof PATHS | string; size?: number; className?: string }) {
  return (
    <svg className={`icon-svg ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={PATHS[name] || ""} />
    </svg>
  );
}

export function Spinner() { return <span className="spin" aria-hidden="true" />; }

/** Placeholder shown while a page's data loads, so the layout doesn't jump. */
export function Skeleton({ tiles = false, rows = 6 }: { tiles?: boolean; rows?: number }) {
  return (
    <div className="skeleton" role="status" aria-label="Loading">
      <i style={{ width: 220, height: 22 }} />
      {tiles && <div className="row">{Array.from({ length: 5 }, (_, i) => <i key={i} className="big" />)}</div>}
      {Array.from({ length: rows }, (_, i) => <i key={i} style={{ height: 34 }} />)}
    </div>
  );
}
