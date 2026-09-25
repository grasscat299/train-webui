import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { MetricResult } from '../api';

const COLORS = ['#2563eb', '#dc2626', '#16a34a', '#d97706', '#7c3aed', '#0891b2', '#db2777', '#65a30d'];

export interface MetricSeries {
  runId: string;
  label?: string;
  points: MetricResult[];
}

// 数値ラベル用の折れ線グラフ。x軸はstep。複数runを渡せば重ねて表示する（Resultタブ用）。
export function MetricChart({ series }: { series: MetricSeries[] }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart margin={{ top: 8, right: 24, bottom: 8, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="step" type="number" allowDuplicatedCategory={false} />
        <YAxis />
        <Tooltip />
        <Legend />
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
