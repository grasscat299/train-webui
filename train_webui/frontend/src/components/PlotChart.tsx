import { CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from 'recharts';
import { useEffect, useState } from 'react';
import { api } from '../api';
import type { BinnedPlot, Plot } from '../api';
import { ScaleToggle } from './ScaleToggle';
import type { AxisScale } from './ScaleToggle';

import { CHART_MARGIN, COLORS, LEGEND_STYLE, TICK, TOOLTIP_STYLE, niceLogDomain } from './chartStyle';

export interface PlotEntry {
  runId: string;
  runLabel: string; // 凡例用の "表示名 (group)"
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
// 見出し（ラベル・title）はパネル側（PanelSections）で出すので、ここでは注記とグラフだけ描く。
export function PlotChart({ entries }: { entries: PlotEntry[] }) {
  // 画面上で切り替えた軸のスケール。null なら define_plot で送られた定義に従う
  const [xOverride, setXOverride] = useState<AxisScale | null>(null);
  const [yOverride, setYOverride] = useState<AxisScale | null>(null);
  // 平均表示（line だけ）。none=そのまま / epoch=config の steps_per_epoch ごと / width=x の幅ごと
  // 横軸が step の line（train/loss など）は、最初から epoch ごとの平均にする。epoch/loss や推論時の曲線など、
  // 横軸が step でないものは平均しない（意味が変わるため）。config に steps_per_epoch が無ければ、そのまま表示して理由を出す
  const [avgMode, setAvgMode] = useState<'none' | 'epoch' | 'width'>(() =>
    entries[0]?.plot.kind === 'line' && /^step/i.test(entries[0].plot.xlabel.trim()) ? 'epoch' : 'none',
  );
  const [avgWidth, setAvgWidth] = useState('100');
  const label = entries[0]?.plot.label ?? '';
  const bin = avgMode === 'epoch' ? 'epoch' : avgWidth;
  const binValid = avgMode === 'epoch' || Number(avgWidth) > 0;
  const runKey = entries.map((e) => e.runId).join(',');
  const [binned, setBinned] = useState<Record<string, BinnedPlot>>({});
  const [binError, setBinError] = useState<string | null>(null);
  const isLine = entries.length > 0 && entries[0].plot.kind === 'line';
  const averaging = isLine && avgMode !== 'none' && binValid;

  // 平均は全点から backend が作る（表示用に間引いた点を平均すると値が歪むため）。学習中は更新されるので定期的に取り直す
  useEffect(() => {
    if (!averaging) {
      setBinned({});
      setBinError(null);
      return;
    }
    let cancelled = false;
    let timer: number | undefined;
    const load = async () => {
      try {
        const got = await Promise.all(
          runKey.split(',').map(async (id) => [id, await api.getBinnedPlot(id, label, bin)] as const),
        );
        if (cancelled) return;
        setBinned(Object.fromEntries(got));
        setBinError(null);
      } catch (e) {
        if (!cancelled) {
          setBinned({});
          setBinError(e instanceof Error ? e.message : String(e));
        }
      } finally {
        if (!cancelled) timer = window.setTimeout(load, 3000);
      }
    };
    void load();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [averaging, runKey, label, bin]);

  if (entries.length === 0) return <div className="no-image">データなし</div>;

  // 軸の定義は updated_at が最新のものを使う
  const def = entries.map((e) => e.plot).reduce((a, b) => (b.updated_at > a.updated_at ? b : a));
  const xscale = xOverride ?? def.xscale;
  const yscale = yOverride ?? def.yscale;
  const defsDiffer = new Set(entries.map((e) => defKey(e.plot))).size > 1;
  const onlyDefault = entries.every((e) => e.plot.series.every((s) => s.name === 'default'));

  let hiddenOnLog = 0;
  const notes: string[] = [];
  const binnedActive = averaging && Object.keys(binned).length > 0;
  const lines = entries.flatMap((e) =>
    (binnedActive ? binned[e.runId]?.series ?? [] : e.plot.series).map((s) => {
      const visible = s.points.filter(
        ([x, y]) => !(xscale === 'log' && x <= 0) && !(yscale === 'log' && y <= 0),
      );
      hiddenOnLog += s.points.length - visible.length;
      const name = onlyDefault ? e.runLabel : `${s.name} @ ${e.runLabel}`;
      if ('downsampled' in s && s.downsampled) notes.push(`間引き表示中: ${name}（元 ${s.total_points} 点 → 表示 ${s.points.length} 点）`);
      return { key: `${e.runId}/${s.name}`, name, data: visible.map(([x, y]) => ({ x, y })) };
    }),
  );
  if (hiddenOnLog > 0) notes.push(`log軸で表示できない0以下の点 ${hiddenOnLog} 個を省略`);

  if (binnedActive) {
    const w = Object.values(binned)[0];
    notes.push(w.epoch ? `epoch ごとの平均（${w.width} step）` : `x を ${w.width} ごとにした平均`);
  }
  if (binError) notes.push(`平均を出せません: ${binError}`);
  // 平均中の x は定義の範囲・ラベルとは単位が違うことがある（epoch 番号など）ので、範囲指定は使わない
  const xlim = binnedActive ? null : def.xlim;
  const xlabel = binnedActive && binned[entries[0].runId]?.epoch ? 'epoch' : def.xlabel;

  const scaleOf = (s: 'linear' | 'log') => (s === 'log' ? 'log' : 'linear');
  // 範囲が指定されていればそれ。無ければ log 軸だけきりのいい値まで広げる（linear 軸は Recharts が丸める）
  const domainOf = (lim: [number, number] | null, scale: AxisScale, values: number[]) =>
    lim ? lim : scale === 'log' ? niceLogDomain(values) : (['auto', 'auto'] as [string, string]);

  return (
    <div className="plot-chart">
      <div className="plot-chart-head">
        {defsDiffer && <span className="plot-chart-note">runによって軸の定義が異なります</span>}
        {notes.map((n) => (
          <span key={n} className="plot-chart-note">{n}</span>
        ))}
        {isLine && (
          <span className="avg-toggle" title="x を一定の幅ごとにまとめて平均して表示する（表示だけの切り替え）">
            平均:
            <select value={avgMode} onChange={(e) => setAvgMode(e.target.value as 'none' | 'epoch' | 'width')}>
              <option value="none">なし</option>
              <option value="epoch">epoch ごと</option>
              <option value="width">幅を指定</option>
            </select>
            {avgMode === 'width' && (
              <input
                type="number"
                min="1"
                value={avgWidth}
                onChange={(e) => setAvgWidth(e.target.value)}
                aria-label="平均する幅（x の単位）"
              />
            )}
          </span>
        )}
        <span className="scale-toggles">
          <ScaleToggle axis="X" value={xscale} onChange={setXOverride} />
          <ScaleToggle axis="Y" value={yscale} onChange={setYOverride} />
        </span>
      </div>
      <div className="chart-box">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart margin={CHART_MARGIN}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis
              dataKey="x"
              type="number"
              scale={scaleOf(xscale)}
              domain={domainOf(xlim, xscale, lines.flatMap((l) => l.data.map((d) => d.x)))}
              allowDataOverflow={xlim !== null}
              tick={TICK}
              label={xlabel ? { value: xlabel, position: 'insideBottom', offset: -4, style: TICK } : undefined}
            />
            <YAxis
              dataKey="y"
              type="number"
              scale={scaleOf(yscale)}
              domain={domainOf(def.ylim, yscale, lines.flatMap((l) => l.data.map((d) => d.y)))}
              allowDataOverflow={def.ylim !== null}
              tick={TICK}
              label={def.ylabel ? { value: def.ylabel, angle: -90, position: 'insideLeft', style: TICK } : undefined}
            />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Legend wrapperStyle={LEGEND_STYLE} iconSize={8} />
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
    </div>
  );
}
