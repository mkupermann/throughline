import { useId, useMemo, useState } from "react";
import { formatDay } from "@/lib/format";

interface Point {
  day: string;
  n: number;
}

/**
 * Single-series line + soft area over a dense daily range.
 *
 * Per the dataviz rules: one series so no legend (the title names it), 2px
 * stroke, gaps filled with zero rather than interpolated across (a missing
 * day is a real zero here, not unknown data), recessive axis, and a crosshair
 * tooltip because an HTML chart that cannot be interrogated is a picture.
 */
export function Sparkline({
  data,
  days,
  height = 96,
  label,
}: {
  data: Point[];
  days: number;
  height?: number;
  label: string;
}) {
  const titleId = useId();
  const [hover, setHover] = useState<number | null>(null);

  // Densify: the API returns only days that had activity.
  const series = useMemo(() => {
    const byDay = new Map(data.map((d) => [d.day, d.n]));
    const out: Point[] = [];
    const today = new Date();
    for (let i = days - 1; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      out.push({ day: key, n: byDay.get(key) ?? 0 });
    }
    return out;
  }, [data, days]);

  const w = 720;
  const h = height;
  const padY = 8;
  // Inset horizontally too: at x=0 and x=w half of a 2px stroke falls
  // outside the viewBox and the first and last points render clipped flat.
  const padX = 3;
  const plotW = w - padX * 2;
  const max = Math.max(1, ...series.map((p) => p.n));
  const stepX = series.length > 1 ? plotW / (series.length - 1) : plotW;
  const x = (i: number) => padX + i * stepX;
  const y = (n: number) => padY + (1 - n / max) * (h - padY * 2);

  const line = series.map((p, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(p.n)}`).join(" ");
  const area = `${line} L${x(series.length - 1)},${h} L${x(0)},${h} Z`;

  const total = series.reduce((a, b) => a + b.n, 0);
  const active = hover !== null ? series[hover] : null;

  return (
    <figure className="spark" aria-labelledby={titleId}>
      <figcaption id={titleId} className="spark-caption">
        <span>{label}</span>
        <span className="spark-total tabular">{total} total</span>
      </figcaption>

      <div className="spark-plot">
        <svg
          viewBox={`0 0 ${w} ${h}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`${label}: ${total} conversations over the last ${days} days, peak ${max} in a day.`}
          onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const rel = ((e.clientX - rect.left) / rect.width) * w - padX;
            setHover(Math.max(0, Math.min(series.length - 1, Math.round(rel / stepX))));
          }}
        >
          <path d={area} className="spark-area" />
          <path d={line} className="spark-line" />
          {active && (
            <>
              <line
                x1={x(hover!)}
                x2={x(hover!)}
                y1={0}
                y2={h}
                className="spark-crosshair"
              />
              <circle cx={x(hover!)} cy={y(active.n)} r={4} className="spark-dot" />
            </>
          )}
        </svg>

        {active && (
          <div
            className="spark-tip"
            style={{ left: `${(x(hover!) / w) * 100}%` }}
            role="status"
          >
            <strong className="tabular">{active.n}</strong> on {formatDay(active.day)}
          </div>
        )}
      </div>
    </figure>
  );
}
