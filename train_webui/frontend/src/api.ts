// CONTRACT.md §2.1, §2.2 に定義された型とAPI呼び出しをここに集約する。
// VITE_MOCK=1 のときは mock.ts のモック実装を使う（backendが無くても動作確認できるようにする）。

export type RunStatus = 'draft' | 'running' | 'finished' | 'failed' | 'stopped' | 'lost';
export type JobStatus = 'running' | 'finished' | 'failed' | 'stopped';
export type JobKind = 'train' | 'infer';
export type Origin = 'webui' | 'cli';

export interface RunSummary {
  run_id: string;
  display_name: string; // group 内で作成順に振られる表示名（v1, v2, ...）
  project: string;
  group: string; // group_id（config の [wandb].group・checkpoint 名 model_{group_id}.pt に使う固定のID）
  group_name: string; // group の表示名（編集できる）
  comment: string; // run へのコメント（メモ）。いつでも編集できる
  origin: Origin;
  status: RunStatus;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
}

export interface Job {
  job_id: string;
  run_id: string;
  kind: JobKind;
  status: JobStatus;
  started_at: string;
  ended_at: string | null;
  exit_code: number | null;
  error: string | null;
}

export interface Run extends RunSummary {
  // group（=モデル構成）の説明。Markdown。Claude がモデルの作りを知るための文書
  group_description: string;
  config_toml: string;
  git_commit: string | null;
  git_diff: string | null;
  git_untracked: string | null;
  git_info_source: 'sync' | 'last_sync' | null;
  hostname: string | null;
  pid: number | null;
  last_heartbeat_at: string | null;
  exit_code: number | null;
  error: string | null;
  jobs: Job[];
}

export interface MetricResult {
  id: number;
  run_id: string;
  kind: 'metric';
  label: string;
  step: number | null;
  value: number;
  source: string;
  sent_at: string;
}

export interface ImageResult {
  id: number;
  run_id: string;
  kind: 'image';
  label: string;
  step: number | null;
  image_url: string;
  source: string;
  sent_at: string;
}

// 表の1マス（表 label の中の name 行）。同じ name が複数あれば sent_at が新しいものを表に出す。
export interface TableResult {
  id: number;
  run_id: string;
  kind: 'table';
  label: string;
  name: string;
  step: number | null;
  value: number;
  source: string;
  sent_at: string;
}

export type Result = MetricResult | ImageResult | TableResult;

// 軸つきグラフ（design.md §5.3）。downsampled=true の系列は points が間引き済みの全体（差分ではない）。
export interface PlotSeries {
  name: string;
  points: [number, number][];
  total_points: number;
  downsampled: boolean;
}

export interface Plot {
  run_id: string;
  label: string;
  kind: 'line' | 'scatter';
  defined: boolean;
  title: string | null;
  xlabel: string;
  ylabel: string;
  xscale: 'linear' | 'log';
  yscale: 'linear' | 'log';
  xlim: [number, number] | null;
  ylim: [number, number] | null;
  updated_at: string;
  series: PlotSeries[];
  last_point_id: number | null;
}

export interface GroupNode {
  group: string; // group_id
  group_name: string;
  runs: RunSummary[];
}

export interface ProjectNode {
  project: string;
  workspace: string; // プロジェクト（categories.toml）
  category: string; // カテゴリー（categories.toml。無ければ「未分類」）
  groups: GroupNode[];
}

export interface ActiveInfo {
  run_id: string;
  job_id: string | null;
  kind: JobKind;
  origin: Origin;
}

export interface StateResp {
  busy: boolean;
  active: ActiveInfo | null;
}

export interface LabelInfo {
  label: string;
  kind: 'metric' | 'image' | 'table' | 'plot';
}

// Result タブで名前を付けて残した比較（比較する run と、表示中・非表示のラベル）
export interface Comparison {
  id: string;
  name: string;
  run_ids: string[];
  labels: string[];
  hidden_labels: string[];
  created_at: string;
  updated_at: string;
}

export type ComparisonBody = Pick<Comparison, 'run_ids' | 'labels' | 'hidden_labels'>;

export interface JobLogResp {
  data: string;
  offset: number;
  done: boolean;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export interface Api {
  getTree(): Promise<ProjectNode[]>;
  rescan(): Promise<ProjectNode[]>;
  getState(): Promise<StateResp>;
  // newGroup=true なら group は不要（group_id と group_name は backend が自動で振る）
  createDraft(project: string, group: string | null, newGroup: boolean): Promise<Run>;
  renameGroup(project: string, group: string, groupName: string): Promise<void>;
  updateComment(runId: string, comment: string): Promise<Run>;
  updateGroupDescription(project: string, group: string, description: string): Promise<void>;
  getRun(runId: string): Promise<Run>;
  updateConfig(runId: string, configToml: string): Promise<Run>;
  startRun(runId: string): Promise<Run>;
  stopRun(runId: string): Promise<Run>;
  deleteRun(runId: string): Promise<void>;
  groupInfer(project: string, group: string): Promise<Job>;
  getJobLog(jobId: string, offset: number): Promise<JobLogResp>;
  getResults(runId: string, kind?: 'metric' | 'image' | 'table', label?: string): Promise<Result[]>;
  getLabels(runIds: string[]): Promise<LabelInfo[]>;
  getPlots(runId: string, afterId?: number | null): Promise<Plot[]>;
  // 画面で今見ている構成を server に送る（Claude が `cli view` で読む）
  listComparisons(): Promise<Comparison[]>;
  createComparison(name: string, body: ComparisonBody): Promise<Comparison>;
  // 渡した項目だけ更新する（名前だけ変える、構成だけ上書きする）
  updateComparison(id: string, patch: Partial<ComparisonBody> & { name?: string }): Promise<Comparison>;
  deleteComparison(id: string): Promise<void>;
  putView(tab: 'train' | 'result', runIds: string[], labels: string[]): Promise<void>;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      // レスポンスボディがJSONでない場合はそのまま
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const realApi: Api = {
  getTree: () => request<ProjectNode[]>('/api/tree'),
  rescan: () => request<ProjectNode[]>('/api/rescan', { method: 'POST' }),
  getState: () => request<StateResp>('/api/state'),
  createDraft: (project, group, newGroup) =>
    request<Run>('/api/runs/draft', {
      method: 'POST',
      body: JSON.stringify({ project, group, new_group: newGroup }),
    }),
  updateComment: (runId, comment) =>
    request<Run>(`/api/runs/${encodeURIComponent(runId)}/comment`, {
      method: 'PUT',
      body: JSON.stringify({ comment }),
    }),
  updateGroupDescription: (project, group, description) =>
    request<void>('/api/groups/description', {
      method: 'PUT',
      body: JSON.stringify({ project, group, description }),
    }),
  renameGroup: (project, group, groupName) =>
    request<void>('/api/groups', {
      method: 'PUT',
      body: JSON.stringify({ project, group, group_name: groupName }),
    }),
  getRun: (runId) => request<Run>(`/api/runs/${encodeURIComponent(runId)}`),
  updateConfig: (runId, configToml) =>
    request<Run>(`/api/runs/${encodeURIComponent(runId)}/config`, {
      method: 'PUT',
      body: JSON.stringify({ config_toml: configToml }),
    }),
  startRun: (runId) => request<Run>(`/api/runs/${encodeURIComponent(runId)}/start`, { method: 'POST' }),
  stopRun: (runId) => request<Run>(`/api/runs/${encodeURIComponent(runId)}/stop`, { method: 'POST' }),
  deleteRun: (runId) => request<void>(`/api/runs/${encodeURIComponent(runId)}`, { method: 'DELETE' }),
  groupInfer: (project, group) =>
    request<Job>('/api/groups/infer', {
      method: 'POST',
      body: JSON.stringify({ project, group }),
    }),
  getJobLog: (jobId, offset) =>
    request<JobLogResp>(`/api/jobs/${encodeURIComponent(jobId)}/log?offset=${offset}`),
  getResults: (runId, kind, label) => {
    const params = new URLSearchParams();
    if (kind) params.set('kind', kind);
    if (label) params.set('label', label);
    const qs = params.toString();
    return request<Result[]>(`/api/runs/${encodeURIComponent(runId)}/results${qs ? `?${qs}` : ''}`);
  },
  getLabels: (runIds) =>
    request<LabelInfo[]>(`/api/labels?run_ids=${encodeURIComponent(runIds.join(','))}`),
  getPlots: (runId, afterId) =>
    request<Plot[]>(
      `/api/runs/${encodeURIComponent(runId)}/plots${afterId != null ? `?after_id=${afterId}` : ''}`,
    ),
  listComparisons: () => request<Comparison[]>('/api/comparisons'),
  createComparison: (name, body) =>
    request<Comparison>('/api/comparisons', { method: 'POST', body: JSON.stringify({ name, ...body }) }),
  updateComparison: (id, patch) =>
    request<Comparison>(`/api/comparisons/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(patch) }),
  deleteComparison: (id) => request<void>(`/api/comparisons/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  putView: (tab, runIds, labels) =>
    request<void>('/api/view', {
      method: 'PUT',
      body: JSON.stringify({ tab, run_ids: runIds, labels }),
    }),
};

// eslint 的に import は先頭に置きたいが、循環を避けるため mock.ts からは型のみを参照させ、
// ここでモック実装を動的にではなく通常 import する（apiMock.ts は api.ts を参照しない）。
import { mockApi } from './mock';

export const api: Api = import.meta.env.VITE_MOCK === '1' ? mockApi : realApi;
