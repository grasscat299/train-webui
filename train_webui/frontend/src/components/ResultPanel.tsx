import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import type { ImageResult, LabelInfo, MetricResult, Plot, ProjectNode, Result } from '../api';
import { MetricChart } from './MetricChart';
import { PlotChart, mergePlots } from './PlotChart';

interface RunMeta {
  project: string;
  group: string;
}

// Result タブ: チェックONのrunを横断してラベルごとに比較表示する（design.md §9）。
export function ResultPanel({ runIds, tree }: { runIds: string[]; tree: ProjectNode[] }) {
  const [labels, setLabels] = useState<LabelInfo[]>([]);
  const [selectedLabels, setSelectedLabels] = useState<Record<string, boolean>>({});
  const [resultsByRun, setResultsByRun] = useState<Record<string, Result[]>>({});
  const [plotsByRun, setPlotsByRun] = useState<Record<string, Plot[]>>({});
  // 差分取得用: runごとの手元の Plot（ポーリング中に最新を参照するため ref に持つ）
  const plotsRef = useRef<Record<string, Plot[]>>({});
  const [error, setError] = useState<string | null>(null);

  const runMetaMap = useMemo(() => {
    const map: Record<string, RunMeta> = {};
    tree.forEach((p) => p.groups.forEach((g) => g.runs.forEach((r) => {
      map[r.run_id] = { project: p.project, group: g.group };
    })));
    return map;
  }, [tree]);

  const key = runIds.slice().sort().join(',');

  useEffect(() => {
    // 比較対象runが変わったら軸つきグラフは取り直す
    plotsRef.current = {};
    setPlotsByRun({});
    if (runIds.length === 0) {
      setLabels([]);
      setResultsByRun({});
      return;
    }
    let cancelled = false;
    let timer: number | undefined;
    const load = async () => {
      try {
        const ls = await api.getLabels(runIds);
        if (cancelled) return;
        setLabels(ls);
        setSelectedLabels((prev) => {
          const next = { ...prev };
          ls.forEach((l) => {
            if (!(l.label in next)) next[l.label] = true;
          });
          return next;
        });
        const entries = await Promise.all(runIds.map(async (id) => [id, await api.getResults(id)] as const));
        if (cancelled) return;
        setResultsByRun(Object.fromEntries(entries));
        const plotEntries = await Promise.all(runIds.map(async (id) => {
          const prev = plotsRef.current[id];
          // 点のidはrun内で通し番号なので、全ラベルの最大の last_point_id 以降を取れば差分になる
          const ids = (prev ?? []).map((p) => p.last_point_id).filter((v): v is number => v !== null);
          const afterId = prev && ids.length > 0 ? Math.max(...ids) : null;
          return [id, mergePlots(prev, await api.getPlots(id, afterId))] as const;
        }));
        if (cancelled) return;
        plotsRef.current = Object.fromEntries(plotEntries);
        setPlotsByRun(plotsRef.current);
        setError(null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) timer = window.setTimeout(load, 3000);
      }
    };
    load();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  if (runIds.length === 0) {
    return <div className="panel-empty">左のツリーで比較対象のrunにチェックを入れてください。</div>;
  }

  return (
    <div className="result-panel">
      {error && <div className="error-banner">{error}</div>}
      <div className="label-checks">
        {labels.map((l) => (
          <label key={l.label} className="label-check">
            <input
              type="checkbox"
              checked={selectedLabels[l.label] ?? true}
              onChange={(e) => setSelectedLabels((prev) => ({ ...prev, [l.label]: e.target.checked }))}
            />
            {l.label} <span className="kind-badge">{l.kind}</span>
          </label>
        ))}
      </div>
      {labels
        .filter((l) => selectedLabels[l.label])
        .map((l) => (
          <section key={l.label} className="result-section">
            <h3>{l.label}</h3>
            {l.kind === 'plot' ? (
              <PlotChart
                label={l.label}
                entries={runIds.flatMap((id) => {
                  const plot = (plotsByRun[id] ?? []).find((p) => p.label === l.label);
                  return plot ? [{ runId: id, runLabel: `${id} (${runMetaMap[id]?.group ?? '-'})`, plot }] : [];
                })}
              />
            ) : l.kind === 'metric' ? (
              <MetricChart
                series={runIds.map((id) => ({
                  runId: id,
                  label: `${id} (${runMetaMap[id]?.group ?? '-'})`,
                  points: (resultsByRun[id] ?? []).filter(
                    (r): r is MetricResult => r.kind === 'metric' && r.label === l.label,
                  ),
                }))}
              />
            ) : (
              <div className="image-row">
                {runIds.map((id) => {
                  const imgs = (resultsByRun[id] ?? []).filter(
                    (r): r is ImageResult => r.kind === 'image' && r.label === l.label,
                  );
                  const latest = imgs.slice().sort((a, b) => a.sent_at.localeCompare(b.sent_at)).at(-1);
                  return (
                    <div key={id} className="image-cell">
                      <div className="image-cell-title">
                        {id} ({runMetaMap[id]?.group ?? '-'})
                      </div>
                      {latest ? <img src={latest.image_url} alt={l.label} /> : <div className="no-image">画像なし</div>}
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        ))}
    </div>
  );
}
