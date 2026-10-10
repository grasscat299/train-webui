import { useEffect, useMemo, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { api } from '../api';
import { waitUntilVisible } from '../hooks/visibility';
import type { ImageResult, LabelInfo, MetricResult, Plot, ProjectNode, Result } from '../api';
import { MetricChart } from './MetricChart';
import { PlotChart, mergePlots } from './PlotChart';
import { PanelSections } from './PanelSections';
import type { PanelItem } from './PanelSections';
import { copyText } from '../copyText';

interface RunMeta {
  project: string;
  group: string;
  displayName: string;
  status: string;
}

// Result タブ: チェックONのrunを横断してラベルごとに比較表示する（design.md §9）。
// 動いていない run の結果を確認し直す間隔（CLI から手で infer した結果などを拾うため、止めはしない）
const IDLE_REFRESH_MS = 60000;

export function ResultPanel({ runIds, tree, activeRunId, selectedLabels, setSelectedLabels, onLabelState }: {
  runIds: string[];
  tree: ProjectNode[];
  activeRunId: string | null;
  // ラベルの表示・非表示（App が持つ。保存した比較の復元でも書き換えるため）
  selectedLabels: Record<string, boolean>;
  setSelectedLabels: Dispatch<SetStateAction<Record<string, boolean>>>;
  // 今表示している・隠しているラベルが変わるたびに通知する（画面の構成を Claude に見せる・比較を保存するため）
  onLabelState: (visible: string[], hidden: string[]) => void;
}) {
  const [labels, setLabels] = useState<LabelInfo[]>([]);
  const [resultsByRun, setResultsByRun] = useState<Record<string, Result[]>>({});
  const [plotsByRun, setPlotsByRun] = useState<Record<string, Plot[]>>({});
  // 差分取得用: runごとの手元の Plot（ポーリング中に最新を参照するため ref に持つ）
  const plotsRef = useRef<Record<string, Plot[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const runMetaMap = useMemo(() => {
    const map: Record<string, RunMeta> = {};
    tree.forEach((p) => p.groups.forEach((g) => g.runs.forEach((r) => {
      map[r.run_id] = { project: p.project, group: g.group_name, displayName: r.display_name, status: r.status };
    })));
    return map;
  }, [tree]);

  const key = runIds.slice().sort().join(',');

  // 学習中・infer 中の run だけ3秒ごとに取り直し、それ以外は最初に1回取ったら IDLE_REFRESH_MS ごとに確認するだけにする。
  // 読み込みループの中から最新の値を見るため ref に持つ
  const liveRef = useRef<(id: string) => boolean>(() => true);
  liveRef.current = (id: string) => id === activeRunId || runMetaMap[id]?.status === 'running';
  const lastFetchedRef = useRef<Record<string, number>>({});
  const wasLiveRef = useRef<Record<string, boolean>>({});

  useEffect(() => {
    // 比較対象runが変わったら軸つきグラフは取り直す
    plotsRef.current = {};
    setPlotsByRun({});
    lastFetchedRef.current = {};
    wasLiveRef.current = {};
    // 表示中のラベル = 画面に出ているグラフ・画像の見出し（チップがONのもの）
  const visibleLabels = labels.filter((l) => selectedLabels[l.label] ?? true).map((l) => l.label);
  const hiddenLabels = labels.filter((l) => !(selectedLabels[l.label] ?? true)).map((l) => l.label);
  const visibleKey = visibleLabels.join('\u0000') + '\u0001' + hiddenLabels.join('\u0000');
  useEffect(() => {
    onLabelState(visibleLabels, hiddenLabels);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleKey]);

  if (runIds.length === 0) {
      setLabels([]);
      setResultsByRun({});
      return;
    }
    let cancelled = false;
    let timer: number | undefined;
    const load = async () => {
      await waitUntilVisible(); // タブが裏にある間は取りに行かない
      if (cancelled) return;
      try {
        const now = Date.now();
        const due = runIds.filter((id) => {
          const live = liveRef.current(id);
          // 動いていた run が止まった直後は、最後の結果を拾うためにもう1回取る
          const justStopped = wasLiveRef.current[id] && !live;
          wasLiveRef.current[id] = live;
          const last = lastFetchedRef.current[id];
          return live || justStopped || last === undefined || now - last >= IDLE_REFRESH_MS;
        });
        if (due.length === 0) return;
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
        const entries = await Promise.all(due.map(async (id) => [id, await api.getResults(id)] as const));
        if (cancelled) return;
        setResultsByRun((prev) => {
          // 比較から外れた run の分は捨て、今回取った run の分だけ置き換える
          const next: Record<string, Result[]> = {};
          runIds.forEach((id) => {
            if (prev[id]) next[id] = prev[id];
          });
          return { ...next, ...Object.fromEntries(entries) };
        });
        const plotEntries = await Promise.all(due.map(async (id) => {
          const prev = plotsRef.current[id];
          // 点のidはrun内で通し番号なので、全ラベルの最大の last_point_id 以降を取れば差分になる
          const ids = (prev ?? []).map((p) => p.last_point_id).filter((v): v is number => v !== null);
          const afterId = prev && ids.length > 0 ? Math.max(...ids) : null;
          return [id, mergePlots(prev, await api.getPlots(id, afterId))] as const;
        }));
        if (cancelled) return;
        plotsRef.current = { ...plotsRef.current, ...Object.fromEntries(plotEntries) };
        setPlotsByRun(plotsRef.current);
        due.forEach((id) => {
          lastFetchedRef.current[id] = now;
        });
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
        resize: 'image' as const,
        content: (
          <div className="image-list">
            {/* run ごとにまとめ、run 名は最初に1回だけ書く。各画像には何枚目か・送り元だけ書く */}
            {runIds.map((id) => {
              const imgs = (resultsByRun[id] ?? [])
                .filter((r): r is ImageResult => r.kind === 'image' && r.label === l.label)
                .sort((a, b) => b.sent_at.localeCompare(a.sent_at));
              if (imgs.length === 0) return null; // 画像の無い run は何も出さない
              return (
                <div key={id} className="image-run">
                  <div className="image-run-title">{runLabel(id)}</div>
                  {imgs.map((img, i) => (
                    <div key={img.id} className="image-item">
                      <div className="image-item-title">
                        {imgs.length > 1 && (
                          <span className="image-item-index">
                            {imgs.length - i}/{imgs.length}
                          </span>
                        )}
                        <span>
                          {img.source}
                          {img.step !== null && <> ・ step {img.step}</>}
                        </span>
                      </div>
                      <a href={img.image_url} target="_blank" rel="noreferrer" title="クリックで元の大きさで開く">
                        <img src={img.image_url} alt={l.label} />
                      </a>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        ),
      };
    });

  return (
    <div className="result-panel">
      {error && <div className="error-banner">{error}</div>}
      <div className="label-checks">
        <button
          className="label-chip copy-runs"
          title="比較中の run_id を1行ずつコピーする（Claude に貼ると、この組み合わせを読める）"
          onClick={async () => {
            if (!(await copyText(runIds.join('\n')))) return;
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1200);
          }}
        >
          {copied ? 'コピーしました' : `run_id をコピー（${runIds.length}件）`}
        </button>
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
