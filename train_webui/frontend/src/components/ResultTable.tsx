import type { Result, TableResult } from '../api';
import { describeMetric } from '../metricDocs';

// 表: 行 = 指標名、列 = run。同じ指標が複数回送られていたら最新の値を出す。
export function ResultTable({ label, columns, docs }: {
  label: string;
  docs?: Record<string, string>; // 指標の説明（行の名前にマウスを乗せると出す）
  columns: { runId: string; runLabel: string; results: Result[] }[];
}) {
  const latest = columns.map((c) => {
    const m = new Map<string, TableResult>();
    c.results.forEach((r) => {
      if (r.kind !== 'table' || r.label !== label) return;
      const prev = m.get(r.name);
      if (!prev || r.sent_at >= prev.sent_at) m.set(r.name, r);
    });
    return m;
  });
  const names: string[] = [];
  latest.forEach((m) => m.forEach((_, name) => { if (!names.includes(name)) names.push(name); }));
  if (names.length === 0) return <div className="no-image">値がありません</div>;
  return (
    <table className="result-table">
      <thead>
        <tr>
          <th />
          {columns.map((c) => <th key={c.runId}>{c.runLabel}</th>)}
        </tr>
      </thead>
      <tbody>
        {names.map((name) => (
          <tr key={name}>
            <th title={docs ? describeMetric(docs, name) : undefined}>{name}</th>
            {latest.map((m, i) => {
              const r = m.get(name);
              return <td key={columns[i].runId}>{r ? (r.value == null ? 'NaN' : Number(r.value.toPrecision(6))) : '-'}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
