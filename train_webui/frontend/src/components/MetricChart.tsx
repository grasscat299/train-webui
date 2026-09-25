import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { MetricResult } from '../api';

import { CHART_HEIGHT, CHART_MARGIN, COLORS, LEGEND_STYLE, TICK, TOOLTIP_STYLE } from './chartStyle';

export interface MetricSeries {
  runId: string;
  label?: string;
  points: MetricResult[];
}

// 数値ラベル用の折れ線グラフ。x軸はstep。複数runを渡せば重ねて表示する（Resultタブ用）。
export function MetricChart({ series }: { series: MetricSeries[] }) {
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <LineChart margin={CHART_MARGIN}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey="step" type="number" allowDuplicatedCategory={false} tick={TICK} />
        <YAxis tick={TICK} />
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        <Legend wrapperStyle={LEGEND_STYLE} iconSize={8} />
        {series.map((s, i) => (
          <Line
            key={s.runId}
            data={s.points.map((p) => ({ step: p.step ?? 0, value: p.value }))}
            dataKey="value"
            name={s.label ?? s.runId}
            stroke={COLORS[i % COLORS.length]}
            dot={false}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
