import { useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import type { Chart } from "../charts";

/** Draws the chart into the leaf div, again when its data object changes or the div's width does. */
function useChartDraw(host: RefObject<HTMLDivElement | null>, chart: Chart, enabled = true) {
  useLayoutEffect(() => {
    const el = host.current;
    if (!el || !enabled) return;
    chart.draw(el);
    let w = el.clientWidth;
    const ro = new ResizeObserver(() =>
      requestAnimationFrame(() => {
        if (el.clientWidth === w) return;
        w = el.clientWidth;
        chart.draw(el);
      }),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, [host, chart, enabled]);
}

// Remembered per chart id for the session, so a re-mounted page keeps the reader's table/chart choice.
const tableShown = new Set<string>();

/**
 * Hosts one hand-written SVG chart (src/charts.ts). React owns everything around it; the chart owns only
 * this leaf div and redraws when its data object changes or the div's width does.
 */
export function ChartView({ id, chart, children }: { id: string; chart: Chart; children?: ReactNode }) {
  const [table, setTable] = useState(() => tableShown.has(id));
  const host = useRef<HTMLDivElement>(null);
  useChartDraw(host, chart, !table);

  const t = table ? chart.table() : null;
  const toggle = () => {
    if (table) tableShown.delete(id);
    else tableShown.add(id);
    setTable(!table);
  };
  return (
    <>
      <div className="chart" ref={host} hidden={table} />
      {children}
      {t && (
        <div className="ctable">
          <table className="tbl">
            <caption className="sr-only">{chart.label}</caption>
            <thead>
              <tr>
                {t.head.map((h, i) => (
                  <th key={i} className={i ? "r" : undefined}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {t.rows.map((r, ri) => (
                <tr key={ri}>
                  {r.map((c, i) => (
                    <td key={i} className={i ? "r num" : undefined}>
                      {c}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="tv">
        <button type="button" className="linkish" aria-pressed={table} onClick={toggle}>
          {table ? "Show chart" : "Show table"}
        </button>
      </div>
    </>
  );
}

/** A chart without its own table toggle, for small multiples that share one table under the grid. */
export function ChartHost({ chart }: { chart: Chart }) {
  const host = useRef<HTMLDivElement>(null);
  useChartDraw(host, chart);
  return <div className="chart" ref={host} />;
}
