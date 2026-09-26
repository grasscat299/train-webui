// MetricChart / PlotChart で共通の見た目（wandb 風の小さめ・線だけのグラフ）。
// 系列の色はテーマ（ライト/ダーク）ごとに styles.css の --series-1..8 で決める
export const COLORS = Array.from({ length: 8 }, (_, i) => `var(--series-${i + 1})`);
export const CHART_MARGIN = { top: 8, right: 12, bottom: 8, left: 0 };
export const TICK = { fontSize: 11, fill: 'var(--muted)' };
export const TOOLTIP_STYLE = { fontSize: 12, borderRadius: 4, border: '1px solid var(--border)', background: 'var(--panel)' };
export const LEGEND_STYLE = { fontSize: 11 };

// log 軸の表示範囲。Recharts は log 軸では範囲をきりのいい値に丸めない（データの最小値〜最大値そのものになる）ので、
// 最小値は下の、最大値は上の「1, 2, 5 × 10^k」まで広げる（例: 0.00179〜0.0155 → 0.001〜0.02）。
// 正の値が無い・全部同じ値なら Recharts の自動に任せる。
const NICE_STEPS = [1, 2, 5, 10];

function niceFloor(v: number): number {
  const base = 10 ** Math.floor(Math.log10(v));
  const step = [...NICE_STEPS].reverse().find((s) => s * base <= v * (1 + 1e-9)) ?? 1;
  return step * base;
}

function niceCeil(v: number): number {
  const base = 10 ** Math.floor(Math.log10(v));
  const step = NICE_STEPS.find((s) => s * base >= v * (1 - 1e-9)) ?? 10;
  return step * base;
}

export function niceLogDomain(values: number[]): [number, number] | ['auto', 'auto'] {
  const positive = values.filter((v) => v > 0 && Number.isFinite(v));
  if (positive.length === 0) return ['auto', 'auto'];
  // 点が多いとスプレッド構文の引数上限に当たるので reduce で求める
  const lo = niceFloor(positive.reduce((a, b) => Math.min(a, b)));
  const hi = niceCeil(positive.reduce((a, b) => Math.max(a, b)));
  return lo < hi ? [lo, hi] : ['auto', 'auto'];
}
