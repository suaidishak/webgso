import { useState } from "react";

type P = { t: string; mw: number | null };

/** Minimal SVG line chart for a forecast series (MW over time). */
export default function LineChart({ points }: { points: P[] }) {
  const [hover, setHover] = useState<number | null>(null);
  if (!points.length) return <p className="muted">No points.</p>;
  const W = 900, H = 240, L = 44, R = 12, T = 12, B = 28;
  const vals = points.map(p => p.mw ?? 0);
  const max = Math.max(1, ...vals) * 1.1;
  const x = (i: number) => L + (i / Math.max(1, points.length - 1)) * (W - L - R);
  const y = (v: number) => T + (1 - v / max) * (H - T - B);
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.mw ?? 0).toFixed(1)}`).join("");
  const area = `${d}L${x(points.length - 1)},${y(0)}L${x(0)},${y(0)}Z`;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => Math.round((max / 1.1) * f * 10) / 10);
  const step = Math.max(1, Math.floor(points.length / 6));
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.round(((px - L) / (W - L - R)) * (points.length - 1));
    setHover(i >= 0 && i < points.length ? i : null);
  };
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img" aria-label="Forecast MW">
        {ticks.map(v => (
          <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="grid" />
            <text x={L - 6} y={y(v) + 4} textAnchor="end" className="axis">{v}</text></g>
        ))}
        {points.map((p, i) => i % step === 0 && (
          <text key={i} x={x(i)} y={H - 8} textAnchor="middle" className="axis">
            {points.length > 1000 ? p.t.slice(5, 10) : points.length > 120 ? `${p.t.slice(8, 10)}/${p.t.slice(5, 7)} ${p.t.slice(11, 16)}` : p.t.slice(11, 16)}
          </text>
        ))}
        <path d={area} className="area" />
        <path d={d} className="line" />
        {hover !== null && (
          <g><line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} className="cursor" />
            <circle cx={x(hover)} cy={y(points[hover].mw ?? 0)} r={4} className="pt" /></g>
        )}
      </svg>
      <div className="chart-readout">
        {hover !== null ? <>{points[hover].t.replace("T", " ").slice(0, 16)} · <b>{points[hover].mw ?? "—"} MW</b></> : <>{points.length} points · MW</>}
      </div>
    </div>
  );
}
