import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { waitUntilVisible } from '../hooks/visibility';
import type { ImageResult, MetricResult, Plot, Result, Run } from '../api';
import { LogView } from './LogView';
import { MetricChart } from './MetricChart';
import { ImageViewer } from './ImageViewer';
import { PlotChart, mergePlots } from './PlotChart';
import { PanelSections } from './PanelSections';
import type { PanelItem } from './PanelSections';

interface Props {
  runId: string | null;
  busy: boolean;
  onRunChanged: () => void;
  onDeleted: () => void;
}

function uniqueLabels(results: Result[]): string[] {
  return Array.from(new Set(results.map((r) => r.label))).sort();
}

// Train タブ: 選択中runの状態(draft/running/finished/...)に応じた表示を切り替える（design.md §8）。
export function TrainPanel({ runId, busy, onRunChanged, onDeleted }: Props) {
  const [run, setRun] = useState<Run | null>(null);
  const [results, setResults] = useState<Result[]>([]);
  const [plots, setPlots] = useState<Plot[]>([]);
  // 軸つきグラフの差分取得用（ポーリング中に最新を参照するため ref に持つ）
  const plotsRef = useRef<Plot[] | undefined>(undefined);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [configDraft, setConfigDraft] = useState('');
  const [configError, setConfigError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [expandedInferJobs, setExpandedInferJobs] = useState<Record<string, boolean>>({});

  useEffect(() => {
    plotsRef.current = undefined;
    setPlots([]);
    if (!runId) {
      setRun(null);
      setResults([]);
      return;
    }
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      await waitUntilVisible();
      if (cancelled) return;
      try {
        const r = await api.getRun(runId);
        if (cancelled) return;
        setRun(r);
        setLoadError(null);
        const rs = await api.getResults(runId);
        if (cancelled) return;
        setResults(rs);
        const prev = plotsRef.current;
        const ids = (prev ?? []).map((p) => p.last_point_id).filter((v): v is number => v !== null);
        const nextPlots = mergePlots(prev, await api.getPlots(runId, prev && ids.length > 0 ? Math.max(...ids) : null));
        if (cancelled) return;
        plotsRef.current = nextPlots;
        setPlots(nextPlots);
        const live = r.status === 'running' || r.jobs.some((j) => j.kind === 'infer' && j.status === 'running');
        // 学習・infer が動いていない run は結果が増えないので、たまに確認するだけにする
        timer = window.setTimeout(tick, live ? 1500 : 30000);
      } catch (e) {
        if (!cancelled) {
          setLoadError(e instanceof Error ? e.message : String(e));
          timer = window.setTimeout(tick, 3000);
        }
      }
    };
    tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [runId, refreshTrigger]);

  useEffect(() => {
    if (run && run.status === 'draft') setConfigDraft(run.config_toml);
    // run_id が変わった時・draft の内容が保存し直された時だけ同期する
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.run_id, run?.status, run?.config_toml]);

  const refresh = () => setRefreshTrigger((t) => t + 1);

  if (!runId) return <div className="panel-empty">左のツリーからrunを選択してください。</div>;
  if (loadError && !run) return <div className="panel-empty error-banner">{loadError}</div>;
  if (!run) return <div className="panel-empty">読み込み中...</div>;

  const metricLabels = uniqueLabels(results.filter((r) => r.kind === 'metric'));
  const imageLabels = uniqueLabels(results.filter((r) => r.kind === 'image'));
  const mainTrainJob = run.jobs.find((j) => j.kind === 'train');
  const inferJobs = run.jobs.filter((j) => j.kind === 'infer');
  const activeInferJob = inferJobs.find((j) => j.status === 'running');

  const handleSaveConfig = async () => {
    setSaving(true);
    setConfigError(null);
    try {
      const updated = await api.updateConfig(run.run_id, configDraft);
      setRun(updated);
      onRunChanged();
    } catch (e) {
      setConfigError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleStart = async () => {
    if (busy) return;
    setSaving(true);
    setConfigError(null);
    try {
      await api.updateConfig(run.run_id, configDraft);
      const started = await api.startRun(run.run_id);
      setRun(started);
      onRunChanged();
      refresh();
    } catch (e) {
      setConfigError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleStop = async () => {
    setSaving(true);
    try {
      const updated = await api.stopRun(run.run_id);
      setRun(updated);
      onRunChanged();
    } catch (e) {
      setConfigError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(`run ${run.group_name} / ${run.display_name}（${run.run_id}）を削除します。よろしいですか？`)) return;
    setSaving(true);
    try {
      await api.deleteRun(run.run_id);
      onDeleted();
    } catch (e) {
      setConfigError(e instanceof Error ? e.message : String(e));
      setSaving(false);
    }
  };

  const panelItems: PanelItem[] = [
    ...plots.map((p) => ({
      label: p.label,
      subtitle: p.title,
      content: <PlotChart entries={[{ runId: run.run_id, runLabel: run.display_name, plot: p }]} />,
    })),
    ...metricLabels.map((label) => ({
      label,
      content: (
        <MetricChart
          series={[{ runId: run.run_id, label: run.display_name, points: results.filter((r): r is MetricResult => r.kind === 'metric' && r.label === label) }]}
        />
      ),
    })),
    ...imageLabels.map((label) => ({
      label,
      resize: 'image' as const,
      content: <ImageViewer images={results.filter((r): r is ImageResult => r.kind === 'image' && r.label === label)} />,
    })),
  ];
  const canDelete = run.status !== 'running';
  const canStopTrain = run.status === 'running' && run.origin === 'webui';

  return (
    <div className="train-detail">
      <div className="run-header">
        <div>
          <div className="run-title">
            <h2 title={run.run_id}>{run.display_name}</h2>
            <span className={`status-pill status-${run.status}`}>{run.status}</span>
            {run.origin === 'cli' && <span className="cli-badge">CLI</span>}
          </div>
          <div className="meta">
            {run.project} / <span title={`group_id: ${run.group}`}>{run.group_name}</span> ・ <span className="run-id">{run.run_id}</span>
            {run.started_at && <> ・ 開始 {run.started_at}</>}
            {run.ended_at && <> ・ 終了 {run.ended_at}</>}
          </div>
        </div>
        <div className="actions">
          {run.status === 'draft' && (
            <>
              <button onClick={handleSaveConfig} disabled={saving}>
                保存
              </button>
              <button className="primary" onClick={handleStart} disabled={saving || busy}>
                学習開始
              </button>
            </>
          )}
          {canStopTrain && (
            <button onClick={handleStop} disabled={saving} className="danger">
              中止
            </button>
          )}
          {canDelete && (
            <button onClick={handleDelete} disabled={saving} className="danger">
              削除
            </button>
          )}
        </div>
      </div>

      {run.status === 'failed' && (
        <div className="error-banner">学習が失敗しました: {run.error ?? mainTrainJob?.error ?? '(詳細不明)'}</div>
      )}
      {run.status === 'lost' && (
        <div className="warn-banner">終了を報告できずに止まった可能性があります（heartbeat途絶、最終: {run.last_heartbeat_at ?? '-'}）。</div>
      )}
      {run.status === 'running' && run.origin === 'cli' && (
        <div className="note">
          CLIから起動されたrunのため、WebUIからの中止・ログ表示はできません（最終heartbeat: {run.last_heartbeat_at ?? '-'}）。
        </div>
      )}
      {run.origin === 'cli' && run.git_info_source === 'last_sync' && (
        <div className="note">
          ※ このrunのコード情報は最後にWebUIが同期した時点のものです（学習用PC上で直接編集されていた場合は実際のコードとずれる可能性があります）。
        </div>
      )}
      {configError && <div className="error-banner">{configError}</div>}


      {activeInferJob && (
        <div className="infer-banner">
          <strong>infer実行中</strong>（job {activeInferJob.job_id}）
          <button onClick={handleStop} disabled={saving}>
            中止
          </button>
          <LogView key={activeInferJob.job_id} jobId={activeInferJob.job_id} />
        </div>
      )}

      {run.status === 'draft' ? (
        <div className="block">
          <div className="block-title">config</div>
          <textarea className="config-editor" value={configDraft} onChange={(e) => setConfigDraft(e.target.value)} rows={22} spellCheck={false} />
          {busy && <div className="note">他のジョブが実行中のため学習を開始できません。</div>}
        </div>
      ) : (
        <>
          <PanelSections items={panelItems} />

          {mainTrainJob && run.origin === 'webui' && (
            <details className="block" open={run.status === 'running' || run.status === 'failed'}>
              <summary className="block-title">ログ</summary>
              <LogView key={mainTrainJob.job_id} jobId={mainTrainJob.job_id} />
            </details>
          )}

          <details className="block">
            <summary className="block-title">config</summary>
            <textarea className="config-editor" value={run.config_toml} readOnly rows={14} />
          </details>

          {inferJobs.length > 0 && (
            <details className="block">
              <summary className="block-title">inferジョブ履歴（{inferJobs.length}）</summary>
              <ul className="infer-history">
                {inferJobs
                  .slice()
                  .reverse()
                  .map((job) => (
                    <li key={job.job_id}>
                      <button className="infer-history-toggle" onClick={() => setExpandedInferJobs((prev) => ({ ...prev, [job.job_id]: !prev[job.job_id] }))}>
                        {expandedInferJobs[job.job_id] ? '▾' : '▸'} {job.job_id} - {job.status}（{job.started_at} 〜 {job.ended_at ?? '実行中'}）
                        {job.status === 'failed' && <span className="error-badge">失敗</span>}
                      </button>
                      {expandedInferJobs[job.job_id] && <LogView jobId={job.job_id} />}
                    </li>
                  ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  );
}
