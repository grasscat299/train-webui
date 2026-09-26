import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useState } from 'react';
import type { MetricResult } from '../api';
import { ScaleToggle } from './ScaleToggle';
import type { AxisScale } from './ScaleToggle';

import { CHART_HEIGHT, CHART_MARGIN, COLORS, LEGEND_STYLE, TICK, TOOLTIP_STYLE } from './chartStyle';

export interface MetricSeries {
  runId: string;
  label?: string;
  points: MetricResult[];
}

// 数値ラベル用の折れ線グラフ。x軸はstep。複数runを渡せば重ねて表示する（Resultタブ用）。
export function MetricChart({ series }: { series: MetricSeries[] }) {
  const [yscale, setYscale] = useState<AxisScale>('linear');
  // log 軸では 0 以下の値は描けないので除く
  const visible = (points: MetricResult[]) => (yscale === 'log' ? points.filter((p) => p.value > 0) : points);
  const hidden = yscale === 'log' ? series.reduce((n, s) => n + s.points.length - visible(s.points).length, 0) : 0;
  return (
    <div>
      <div className="plot-chart-head">
        {hidden > 0 && <span className="plot-chart-note">log軸で表示できない0以下の点 {hidden} 個を省略</span>}
        <span className="scale-toggles">
          <ScaleToggle axis="Y" value={yscale} onChange={setYscale} />
        </span>
      </div>
      <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
        <LineChart margin={CHART_MARGIN}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="step" type="number" allowDuplicatedCategory={false} tick={TICK} />
          <YAxis tick={TICK} scale={yscale} domain={['auto', 'auto']} />
          <Tooltip contentStyle={TOOLTIP_STYLE} />
          <Legend wrapperStyle={LEGEND_STYLE} iconSize={8} />
          {series.map((s, i) => (
            <Line
              key={s.runId}
              data={visible(s.points).map((p) => ({ step: p.step ?? 0, value: p.value }))}
              dataKey="value"
              name={s.label ?? s.runId}
              stroke={COLORS[i % COLORS.length]}
              dot={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
