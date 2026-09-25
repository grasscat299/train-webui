import { CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from 'recharts';
import type { Plot } from '../api';

const COLORS = ['#2563eb', '#dc2626', '#16a34a', '#d97706', '#7c3aed', '#0891b2', '#db2777', '#65a30d'];

export interface PlotEntry {
  runId: string;
  runLabel: string; // 凡例用の "run_id (group)"
  plot: Plot;
}

// 差分で取得した Plot を手元の Plot に反映する。downsampled の系列は置き換え、それ以外は追記する。
export function mergePlots(prev: Plot[] | undefined, next: Plot[]): Plot[] {
  const prevByLabel = new Map((prev ?? []).map((p) => [p.label, p]));
  return next.map((p) => {
    const old = prevByLabel.get(p.label);
    if (!old) return p;
    const oldSeries = new Map(old.series.map((s) => [s.name, s]));
    return {
      ...p,
      series: p.series.map((s) => {
        const o = oldSeries.get(s.name);
        return s.downsampled || !o ? s : { ...s, points: [...o.points, ...s.points] };
      }),
    };
  });
}

// 軸の定義として比較する項目（design.md §9）
function defKey(p: Plot): string {
  return JSON.stringify([p.kind, p.title, p.xlabel, p.ylabel, p.xscale, p.yscale, p.xlim, p.ylim]);
}

// 軸つきグラフ（design.md §5.3, §9）。比較対象runの同じラベルの全系列を1つの軸に重ねる。
export function PlotChart({ label, entries }: { label: string; entries: PlotEntry[] }) {
  if (entries.length === 0) return <div className="no-image">データなし</div>;

  // 軸の定義は updated_at が最新のものを使う
  const def = entries.map((e) => e.plot).reduce((a, b) => (b.updated_at > a.updated_at ? b : a));
  const defsDiffer = new Set(entries.map((e) => defKey(e.plot))).size > 1;
  const onlyDefault = entries.every((e) => e.plot.series.every((s) => s.name === 'default'));

  let hiddenOnLog = 0;
  const notes: string[] = [];
  const lines = entries.flatMap((e) =>
    e.plot.series.map((s) => {
      const visible = s.points.filter(
        ([x, y]) => !(def.xscale === 'log' && x <= 0) && !(def.yscale === 'log' && y <= 0),
      );
      hiddenOnLog += s.points.length - visible.length;
      const name = onlyDefault ? e.runLabel : `${s.name} @ ${e.runLabel}`;
      if (s.downsampled) notes.push(`間引き表示中: ${name}（元 ${s.total_points} 点 → 表示 ${s.points.length} 点）`);
      return { key: `${e.runId}/${s.name}`, name, data: visible.map(([x, y]) => ({ x, y })) };
    }),
  );
  if (hiddenOnLog > 0) notes.push(`log軸で表示できない0以下の点 ${hiddenOnLog} 個を省略`);

  const scaleOf = (s: 'linear' | 'log') => (s === 'log' ? 'log' : 'linear');
  const domainOf = (lim: [number, number] | null) =>
    lim ? lim : (['auto', 'auto'] as [string, string]);

  return (
    <div className="plot-chart">
      <div className="plot-chart-head">
        <span className="plot-chart-title">{def.title ?? label}</span>
        {defsDiffer && <span className="plot-chart-note">runによって軸の定義が異なります</span>}
        {notes.map((n) => (
          <span key={n} className="plot-chart-note">{n}</span>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={280}>
        <ComposedChart margin={{ top: 8, right: 24, bottom: 20, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            dataKey="x"
            type="number"
            scale={scaleOf(def.xscale)}
            domain={domainOf(def.xlim)}
            allowDataOverflow={def.xlim !== null}
            label={def.xlabel ? { value: def.xlabel, position: 'insideBottom', offset: -10 } : undefined}
          />
          <YAxis
            dataKey="y"
            type="number"
            scale={scaleOf(def.yscale)}
            domain={domainOf(def.ylim)}
            allowDataOverflow={def.ylim !== null}
            label={def.ylabel ? { value: def.ylabel, angle: -90, position: 'insideLeft' } : undefined}
          />
          <Tooltip />
          <Legend verticalAlign="top" />
          {lines.map((l, i) =>
            def.kind === 'line' ? (
              <Line
                key={l.key}
                data={l.data}
                dataKey="y"
                name={l.name}
                stroke={COLORS[i % COLORS.length]}
                dot={false}
                isAnimationActive={false}
              />
            ) : (
              <Scatter
                key={l.key}
                data={l.data}
                dataKey="y"
                name={l.name}
                fill={COLORS[i % COLORS.length]}
                isAnimationActive={false}
              />
            ),
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
