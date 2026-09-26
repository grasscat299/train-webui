import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import type { ImageResult, LabelInfo, MetricResult, Plot, ProjectNode, Result } from '../api';
import { MetricChart } from './MetricChart';
import { PlotChart, mergePlots } from './PlotChart';
import { PanelSections } from './PanelSections';
import type { PanelItem } from './PanelSections';

interface RunMeta {
  project: string;
  group: string;
  displayName: string;
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
      map[r.run_id] = { project: p.project, group: g.group_name, displayName: r.display_name };
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

  // 凡例・見出しは表示名で出す（v1 等は group 内でしか一意でないので group も添える）
  const runLabel = (id: string) => `${runMetaMap[id]?.displayName ?? id} (${runMetaMap[id]?.group ?? '-'})`;
  const panelItems: PanelItem[] = labels
    .filter((l) => selectedLabels[l.label])
    .map((l) => {
      if (l.kind === 'plot') {
        const entries = runIds.flatMap((id) => {
          const plot = (plotsByRun[id] ?? []).find((p) => p.label === l.label);
          return plot ? [{ runId: id, runLabel: runLabel(id), plot }] : [];
        });
        // 見出しの title は PlotChart と同じく updated_at が最新の定義のもの
        const latest = entries.map((e) => e.plot).sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
        return { label: l.label, subtitle: latest?.title, content: <PlotChart entries={entries} /> };
      }
      if (l.kind === 'metric') {
        return {
          label: l.label,
          content: (
            <MetricChart
              series={runIds.map((id) => ({
                runId: id,
                label: runLabel(id),
                points: (resultsByRun[id] ?? []).filter(
                  (r): r is MetricResult => r.kind === 'metric' && r.label === l.label,
                ),
              }))}
            />
          ),
        };
      }
      return {
        label: l.label,
        // 画像は run ごとに、送られた全て（infer を何回か実行していればその全部）を新しい順に縦に並べる
        wide: true,
        content: (
          <div className="image-list">
            {runIds.flatMap((id) => {
              const imgs = (resultsByRun[id] ?? [])
                .filter((r): r is ImageResult => r.kind === 'image' && r.label === l.label)
                .sort((a, b) => b.sent_at.localeCompare(a.sent_at));
              if (imgs.length === 0) {
                return [
                  <div key={id} className="image-item">
                    <div className="image-item-title">
                      <span className="image-item-run">{runLabel(id)}</span>
                    </div>
                    <div className="no-image">画像なし</div>
                  </div>,
                ];
              }
              return imgs.map((img, i) => (
                <div key={img.id} className="image-item">
                  <div className="image-item-title">
                    <span className="image-item-run">{runLabel(id)}</span>
                    <span>
                      {img.source} ・ {img.sent_at.replace('T', ' ').slice(0, 19)}
                      {img.step !== null && <> ・ step {img.step}</>}
                      {imgs.length > 1 && <> ・ {imgs.length - i}/{imgs.length}</>}
                    </span>
                  </div>
                  <a href={img.image_url} target="_blank" rel="noreferrer" title="クリックで元の大きさで開く">
                    <img src={img.image_url} alt={l.label} />
                  </a>
                </div>
              ));
            })}
          </div>
        ),
      };
    });

  return (
    <div className="result-panel">
      {error && <div className="error-banner">{error}</div>}
      <div className="label-checks">
        {labels.map((l) => {
          const on = selectedLabels[l.label] ?? true;
          return (
            <button
              key={l.label}
              className={`label-chip${on ? ' on' : ''}`}
              title={`${l.kind}（クリックで表示/非表示）`}
              onClick={() => setSelectedLabels((prev) => ({ ...prev, [l.label]: !on }))}
            >
              {l.label}
            </button>
          );
        })}
      </div>
      <PanelSections items={panelItems} />
    </div>
  );
}
