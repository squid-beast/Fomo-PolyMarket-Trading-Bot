import { useEffect, useRef, useState } from "react";
import { money, shortTime } from "../format";

interface Hover { x: number; y: number; v: number; t: string }

/**
 * Single-series line. The viewBox aspect tracks the container width —
 * a fixed wide viewBox collapses to an unreadable ~70px strip on a phone.
 */
export function EquityChart({ points }: { points: [string, number][] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(1000);
  const [hover, setHover] = useState<Hover | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      if (entry) setW(entry.contentRect.width || 1000);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (points.length < 2)
    return <div ref={ref}><div className="empty">Not enough data points yet.</div></div>;

  const narrow = w < 560;
  const W = narrow ? 520 : 1000;
  const H = narrow ? 300 : 230;
  const P = narrow ? 62 : 54;

  const vals = points.map((p) => p[1]);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.12;
  lo -= pad; hi += pad;

  const X = (i: number) => P + (i * (W - P - 14)) / Math.max(points.length - 1, 1);
  const Y = (v: number) => P / 2 + ((hi - v) / (hi - lo)) * (H - P);
  const line = points.map((p, i) => `${X(i).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }}
           role="img" aria-label="Account equity over time">
        {[0, 0.5, 1].map((f) => {
          const v = lo + (hi - lo) * f;
          return (
            <g key={f}>
              <line x1={P} y1={Y(v)} x2={W - 14} y2={Y(v)} stroke="var(--grid)" strokeWidth={1} />
              <text x={P - 7} y={Y(v) + 4} textAnchor="end"
                    fontSize={narrow ? 15 : 11} fill="var(--muted)">
                ${v.toFixed(0)}
              </text>
            </g>
          );
        })}
        <polyline points={line} fill="none" stroke="var(--series)"
                  strokeWidth={narrow ? 4 : 2} strokeLinejoin="round" strokeLinecap="round" />
        {points.map((p, i) => (
          <circle key={i} cx={X(i)} cy={Y(p[1])} r={narrow ? 14 : 10} fill="transparent"
                  onMouseEnter={(e) => {
                    const r = (e.target as SVGCircleElement).getBoundingClientRect();
                    setHover({ x: r.left, y: r.top, v: p[1], t: shortTime(p[0]) });
                  }}
                  onMouseLeave={() => setHover(null)} />
        ))}
      </svg>
      {hover && (
        <div className="tt" style={{
          left: Math.min(hover.x + 12, window.innerWidth - 170),
          top: Math.max(hover.y - 46, 8), opacity: 1,
        }}>
          <b>{money(hover.v)}</b><br />{hover.t}
        </div>
      )}
    </div>
  );
}
