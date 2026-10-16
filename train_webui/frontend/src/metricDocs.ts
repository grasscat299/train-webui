// 指標・グラフの名前から説明を引く（マウスを乗せたときの表示用）。説明は project の metric_docs.toml（名前 → 説明）。
// 名前の形: "mid/all_killed_rate"（学習中の小評価）、"eval/summary" の行 "all_killed_rate"・"killed_mean"、"train/loss_ope_ce_dir" など。
// まず名前そのまま、次に区分（mid/・eval/・train/）を外した名前、最後に末尾（_rate・_mean・_median）を外した名前で引く。
const SUFFIX: Record<string, string> = {
  rate: '（割合: 該当したシナリオの比率）',
  mean: '（平均）',
  median: '（中央値）',
};

export function describeMetric(docs: Record<string, string>, name: string): string | undefined {
  const section = name.includes('/') ? name.slice(0, name.indexOf('/')) : '';
  const bare = name.includes('/') ? name.slice(name.indexOf('/') + 1) : name;
  const direct = docs[name] ?? docs[bare];
  if (direct) return direct;
  const m = /^(.*)_(rate|mean|median)$/.exec(bare);
  const base = m ? docs[m[1]] : undefined;
  if (!base) return undefined;
  const tail = m ? SUFFIX[m[2]] : '';
  const mid = section === 'mid' ? '\n学習中の小評価（少数のシナリオで測るので値が揺れる。最終の評価は eval/summary）' : '';
  return `${base}${tail}${mid}`;
}
