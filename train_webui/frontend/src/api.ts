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
  code_commit: string | null; // 起動時のコードを残した commit（無ければ null。無い run は同じコードで再実行できない）
  parent_run_id: string | null; // 引き継ぎ学習の元の run（config の init_from）。無ければ null
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

export interface Artifact {
  name: string; // run 内で一意（例 model.pt）
  kind: string; // checkpoint / samples など
  path: string; // 学習用PC上のパス
  size: number | null;
  updated_at: string;
}

// 学習用PC（複数台）の状態（GET /api/machines）。available=false のとき reason に使えない理由が入る
export interface MachineInfo {
  name: string;
  priority: number;
  reachable: boolean;
  own_jobs: number;
  other_active: boolean; // 他の人が使っている（直近も含む）
  gpu_util_pct: number;
  cpu_util_pct: number;
  free_mib: number | null;
  total_mib: number | null;
  available: boolean;
  reason: string | null;
  group: string[] | null;
}

export interface MachinesResp {
  default: string;
  selected: string | null; // 自動選択でいま選ばれる台
  machines: MachineInfo[];
}

export interface Run extends RunSummary {
  machine?: string | null; // 学習を動かした台（古い run は無い = 既定の台）
  artifacts: Artifact[]; // 学習用PCに残った重みなど（本体はここに無く、場所だけ）
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
  downsampled?: boolean; // true の label は、同じ label の結果が間引き済みの全体として返っている（差分ではない）
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

// 差分で取得した結果を手元の結果に反映する。downsampled の label は置き換え、それ以外は追記する。
export function mergeResults(prev: Result[] | undefined, next: Result[]): Result[] {
  const replaced = new Set(next.filter((r) => r.kind === 'metric' && r.downsampled).map((r) => r.label));
  return [...(prev ?? []).filter((r) => !replaced.has(r.label)), ...next];
}

// 次の差分取得の起点（手元の結果の最大 id）。まだ何も無ければ null（全部取る）
export function lastResultId(results: Result[] | undefined): number | null {
  return results && results.length > 0 ? Math.max(...results.map((r) => r.id)) : null;
}

// 画面に出す点数の上限（metric の間引き用）
export const RESULT_MAX_POINTS = 1000;

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
  omitted?: boolean; // true なら points は空（横軸が step の line。平均表示は binned、生の点は getPlots の label 指定で取る）
  last_point_id: number | null;
}

// x を一定の幅ごとにまとめて平均したグラフ（全点から backend が作る）。epoch=true なら x は epoch 番号
export interface BinnedPlot {
  label: string;
  bin: string; // "epoch" か幅の数値
  width: number;
  epoch: boolean;
  series: { name: string; points: [number, number][]; total_points: number }[];
}

export interface GroupNode {
  group: string; // group_id
  group_name: string;
  runs: RunSummary[];
}

export interface ProjectNode {
  project: string;
  stage: string; // 段階（本番 / scratch など。モデルのパスの先頭で決まる）
  workspace: string; // プロジェクト（categories.toml。無ければ「未分類」）
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

export interface MonitorGpu {
  index: number;
  name: string;
  util: number | null; // %
  mem_used_mib: number | null;
  mem_total_mib: number | null;
  temp_c: number | null;
  power_w: number | null;
}

// 学習用PCの使用状況（GET /api/monitor）。cpu_cores は論理コアごとの使用率(%)
export interface MonitorResp {
  cpu_total: number | null;
  cpu_temp_c: number | null; // ℃。センサーが読めない台は null
  cpu_cores: number[];
  gpus: MonitorGpu[];
}

// gpu5 の lab_monitor server の応答（GET /api/lab/now, /api/lab/history。形は lab_monitor の API と同じ）。
// 取得に失敗した台は error だけが入り、数値は無い。in_use は GPU メモリのしきい値で判定した使用中(1)/空き(0)
export interface LabNow {
  name: string;
  title: string;
  label: string;
  ts?: number;
  error?: string;
  cpu?: number | null;
  gpu_util?: number | null;
  vram_used?: number | null;
  vram_total?: number | null;
  temp?: number | null;
  cpu_temp?: number | null;
  load?: number;
  ram_used?: number;
  ram_total?: number;
  in_use?: number | null;
}

export interface LabHistoryRow {
  ts: number;
  ok: number;
  gpu_util: number | null;
  vram_used: number | null;
  vram_total: number | null;
  temp: number | null;
  cpu: number | null;
  cpu_temp: number | null;
  load: number | null;
  ram_used: number | null;
  ram_total: number | null;
  in_use: number | null;
}

export interface Api {
  getTree(): Promise<ProjectNode[]>;
  rescan(): Promise<ProjectNode[]>;
  getState(): Promise<StateResp>;
  // newGroup=true なら group は不要（group_id と group_name は backend が自動で振る）
  createDraft(project: string, group: string | null, newGroup: boolean): Promise<Run>;
  // 既存の run を元にした下書き。continue=その run の重みから続きを学習 / rerun=その run を起動した時点のコードで再実行
  createDraftFrom(project: string, kind: 'continue' | 'rerun', runId: string): Promise<Run>;
  renameGroup(project: string, group: string, groupName: string): Promise<void>;
  updateComment(runId: string, comment: string): Promise<Run>;
  updateGroupDescription(project: string, group: string, description: string): Promise<void>;
  getRun(runId: string): Promise<Run>;
  updateConfig(runId: string, configToml: string): Promise<Run>;
  startRun(runId: string, machine?: string | null): Promise<Run>;
  getMachines(): Promise<MachinesResp>;
  getAlerts(): Promise<{ alerts: { kind: string; message: string; machines: string[]; since: number }[] }>;
  stopRun(runId: string, graceful?: boolean): Promise<Run>;
  deleteRun(runId: string): Promise<void>;
  groupInfer(project: string, group: string): Promise<Job>;
  // その run の重みで推論する（重みが登録された、学習の終わった run だけ）
  runInfer(project: string, runId: string): Promise<Job>;
  getJobLog(jobId: string, offset: number): Promise<JobLogResp>;
  // afterId を渡すと、それより後の結果だけ返す（差分）。maxPoints を渡すと、それを超える metric は間引き済みの全体を返す
  getResults(
    runId: string, kind?: 'metric' | 'image' | 'table', label?: string,
    opts?: { afterId?: number | null; maxPoints?: number },
  ): Promise<Result[]>;
  getLabels(runIds: string[]): Promise<LabelInfo[]>;
  // 指標・グラフの説明（名前 → 説明。project の metric_docs.toml）。マウスを乗せたときの表示用
  getMetricDocs(project: string): Promise<Record<string, string>>;
  getMonitor(machine?: string | null): Promise<MonitorResp>;
  getLabNow(): Promise<LabNow[]>;
  getLabHistory(machine: string, hours: number): Promise<LabHistoryRow[]>;
  // omitStepLines: 横軸が step の line は点を返さない。label: そのグラフだけ取る
  getPlots(runId: string, afterId?: number | null, opts?: { omitStepLines?: boolean; label?: string }): Promise<Plot[]>;
  // line のグラフを x の幅ごとに平均して取る。bin は "epoch"（config の steps_per_epoch）か幅の数値
  getBinnedPlot(runId: string, label: string, bin: string): Promise<BinnedPlot>;
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
  createDraftFrom: (project, kind, runId) =>
    request<Run>('/api/runs/draft', {
      method: 'POST',
      body: JSON.stringify({
        project, new_group: false,
        ...(kind === 'continue' ? { parent_run_id: runId } : { rerun_of: runId }),
      }),
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
  startRun: (runId, machine) =>
    request<Run>(`/api/runs/${encodeURIComponent(runId)}/start${machine ? `?machine=${encodeURIComponent(machine)}` : ''}`, {
      method: 'POST',
    }),
  getMachines: () => request<MachinesResp>('/api/machines'),
  getAlerts: () => request<{ alerts: { kind: string; message: string; machines: string[]; since: number }[] }>('/api/alerts'),
  stopRun: (runId, graceful = false) =>
    request<Run>(`/api/runs/${encodeURIComponent(runId)}/stop${graceful ? '?graceful=true' : ''}`, { method: 'POST' }),
  deleteRun: (runId) => request<void>(`/api/runs/${encodeURIComponent(runId)}`, { method: 'DELETE' }),
  groupInfer: (project, group) =>
    request<Job>('/api/groups/infer', {
      method: 'POST',
      body: JSON.stringify({ project, group }),
    }),
  runInfer: (project, runId) =>
    request<Job>('/api/groups/infer', {
      method: 'POST',
      body: JSON.stringify({ project, run_id: runId }),
    }),
  getJobLog: (jobId, offset) =>
    request<JobLogResp>(`/api/jobs/${encodeURIComponent(jobId)}/log?offset=${offset}`),
  getResults: (runId, kind, label, opts) => {
    const params = new URLSearchParams();
    if (kind) params.set('kind', kind);
    if (label) params.set('label', label);
    if (opts?.afterId != null) params.set('after_id', String(opts.afterId));
    if (opts?.maxPoints != null) params.set('max_points', String(opts.maxPoints));
    const qs = params.toString();
    return request<Result[]>(`/api/runs/${encodeURIComponent(runId)}/results${qs ? `?${qs}` : ''}`);
  },
  getMonitor: (machine) => request<MonitorResp>(`/api/monitor${machine ? `?machine=${encodeURIComponent(machine)}` : ''}`),
  getLabNow: () => request<LabNow[]>('/api/lab/now'),
  getLabHistory: (machine, hours) => request<LabHistoryRow[]>(`/api/lab/history?machine=${encodeURIComponent(machine)}&hours=${hours}`),
  getMetricDocs: (project) => request<Record<string, string>>(`/api/metric_docs?project=${encodeURIComponent(project)}`),
  getLabels: (runIds) =>
    request<LabelInfo[]>(`/api/labels?run_ids=${encodeURIComponent(runIds.join(','))}`),
  getPlots: (runId, afterId, opts) => {
    const params = new URLSearchParams();
    if (afterId != null) params.set('after_id', String(afterId));
    if (opts?.omitStepLines) params.set('omit_step_lines', 'true');
    if (opts?.label) params.set('label', opts.label);
    const qs = params.toString();
    return request<Plot[]>(`/api/runs/${encodeURIComponent(runId)}/plots${qs ? `?${qs}` : ''}`);
  },
  getBinnedPlot: (runId, label, bin) =>
    request<BinnedPlot>(
      `/api/runs/${encodeURIComponent(runId)}/plots/binned?label=${encodeURIComponent(label)}&bin=${encodeURIComponent(bin)}`,
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
