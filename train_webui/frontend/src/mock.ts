// VITE_MOCK=1 用のモック実装。backendなしで画面の状態遷移を一通り確認できるようにする。
// api.ts の Api インターフェースを満たす（api.ts からは型のみをimportし、循環参照を避ける）。
import type {
  BinnedPlot,
  Api,
  Comparison,
  ProjectNode,
  GroupNode,
  StateResp,
  Run,
  RunSummary,
  Job,
  JobKind,
  Result,
  MetricResult,
  ImageResult,
  LabelInfo,
  JobLogResp,
  Plot,
} from './api';

type StoredRun = Omit<Run, 'jobs' | 'group_name' | 'group_description' | 'artifacts'>;

// group の表示名（backend の groups テーブル相当）。未登録の group は group_id をそのまま表示名にする
const groupNames = new Map<string, string>();
const groupKeyOf = (project: string, group: string) => `${project}\u0000${group}`;
const groupNameOf = (project: string, group: string) => groupNames.get(groupKeyOf(project, group)) ?? group;
const groupDescriptions = new Map<string, string>();
const groupDescriptionOf = (project: string, group: string) => groupDescriptions.get(groupKeyOf(project, group)) ?? '';

let seq = 0;
function nextId(): number {
  seq += 1;
  return seq;
}

function isoNow(): string {
  return new Date().toISOString();
}

function hoursAgo(h: number): Date {
  return new Date(Date.now() - h * 3600_000);
}

function runIdFromDate(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

function dummyImage(label: string, step: number | null, color: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="220" height="160">
    <rect width="100%" height="100%" fill="${color}" />
    <text x="10" y="24" font-size="14" fill="#fff">${label}</text>
    <text x="10" y="46" font-size="14" fill="#fff">step=${step ?? '-'}</text>
    <text x="10" y="68" font-size="11" fill="#fff">${new Date().toLocaleTimeString('ja-JP')}</text>
  </svg>`;
  return `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svg)))}`;
}

const SAMPLE_CONFIG = `[wandb]
project = "sample"
group = "wm_v1_d128_L6"

[train]
epochs = 100
lr = 0.0003
batch_size = 64
`;

function setGroupInToml(toml: string, group: string): string {
  if (/group\s*=\s*"/.test(toml)) {
    return toml.replace(/group\s*=\s*"[^"]*"/, `group = "${group}"`);
  }
  return `${toml}\n[wandb]\ngroup = "${group}"\n`;
}

function extractGroup(toml: string): string | null {
  if (!/\[wandb\]/.test(toml)) return null;
  const m = toml.match(/group\s*=\s*"([^"]*)"/);
  return m ? m[1] : null;
}

function latestByCreated<T extends { created_at: string }>(list: T[]): T {
  return list.slice().sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
}

// ---- ストア ----
const runs = new Map<string, StoredRun>();
const jobsByRun = new Map<string, Job[]>();
const resultsByRun = new Map<string, Result[]>();
const logsByJob = new Map<string, string>();
const tickCounts = new Map<string, number>();
const emptyProjects: string[] = [];
let nextResultId = 1;

// backend と同じく group 内の最大番号 + 1 で表示名を振る
function nextDisplayName(project: string, group: string): string {
  const used = Array.from(runs.values())
    .filter((r) => r.project === project && r.group === group)
    .map((r) => Number(r.display_name.slice(1)));
  return `v${Math.max(0, ...used) + 1}`;
}

function makeRun(fields: Omit<StoredRun, 'display_name' | 'comment' | 'parent_run_id' | 'code_commit'>): StoredRun {
  const run: StoredRun = { ...fields, display_name: nextDisplayName(fields.project, fields.group), comment: '', parent_run_id: null, code_commit: null };
  runs.set(run.run_id, run);
  if (!jobsByRun.has(run.run_id)) jobsByRun.set(run.run_id, []);
  if (!resultsByRun.has(run.run_id)) resultsByRun.set(run.run_id, []);
  return run;
}

function addJob(runId: string, job: Job) {
  const list = jobsByRun.get(runId) ?? [];
  list.push(job);
  jobsByRun.set(runId, list);
}

function pushTable(runId: string, label: string, name: string, value: number, source: string, sentAt = isoNow()) {
  const list = resultsByRun.get(runId) ?? [];
  list.push({ id: nextResultId++, run_id: runId, kind: 'table', label, name, step: null, value, source, sent_at: sentAt });
  resultsByRun.set(runId, list);
}

function pushMetric(runId: string, label: string, step: number | null, value: number, source: string, sentAt = isoNow()) {
  const list = resultsByRun.get(runId) ?? [];
  const r: MetricResult = { id: nextResultId++, run_id: runId, kind: 'metric', label, step, value, source, sent_at: sentAt };
  list.push(r);
  resultsByRun.set(runId, list);
}

// 軸つきグラフ: 点には backend と同じく通し番号の id を振る
type MockPlotDef = Omit<Plot, 'series' | 'last_point_id'>;
interface MockPlotPoint { id: number; series: string; x: number; y: number }
const plotsByRun = new Map<string, Map<string, { def: MockPlotDef; points: MockPlotPoint[] }>>();
let nextPointId = 1;
const MOCK_MAX_POINTS = 1000;

function definePlot(runId: string, def: Omit<MockPlotDef, 'run_id' | 'defined' | 'updated_at'>) {
  const byLabel = plotsByRun.get(runId) ?? new Map();
  const prev = byLabel.get(def.label);
  byLabel.set(def.label, { def: { ...def, run_id: runId, defined: true, updated_at: isoNow() }, points: prev?.points ?? [] });
  plotsByRun.set(runId, byLabel);
}

function pushPlotPoints(runId: string, label: string, series: string, xs: number[], ys: number[]) {
  const entry = plotsByRun.get(runId)?.get(label);
  if (!entry) return;
  xs.forEach((x, i) => entry.points.push({ id: nextPointId++, series, x, y: ys[i] }));
}

function pushImage(runId: string, label: string, step: number | null, imageUrl: string, source: string, sentAt = isoNow()) {
  const list = resultsByRun.get(runId) ?? [];
  const r: ImageResult = { id: nextResultId++, run_id: runId, kind: 'image', label, step, image_url: imageUrl, source, sent_at: sentAt };
  list.push(r);
  resultsByRun.set(runId, list);
}

function appendLog(jobId: string, text: string) {
  logsByJob.set(jobId, (logsByJob.get(jobId) ?? '') + text);
}

function findJob(jobId: string): Job | undefined {
  for (const list of jobsByRun.values()) {
    const found = list.find((j) => j.job_id === jobId);
    if (found) return found;
  }
  return undefined;
}

function generateRunId(): string {
  const base = runIdFromDate(new Date());
  let candidate = base;
  let n = 2;
  while (runs.has(candidate)) {
    candidate = `${base}_${n}`;
    n += 1;
  }
  return candidate;
}

function generateJobId(runId: string, kind: JobKind): string {
  return `${runId}__${kind}__${nextId()}`;
}

function getOrThrow(runId: string): StoredRun {
  const run = runs.get(runId);
  if (!run) throw new Error(`run が見つかりません: ${runId}`);
  return run;
}

function cloneRun(run: StoredRun): Run {
  return { ...run, group_name: groupNameOf(run.project, run.group), group_description: groupDescriptionOf(run.project, run.group), artifacts: [], jobs: (jobsByRun.get(run.run_id) ?? []).slice() };
}

function toSummary(run: StoredRun): RunSummary {
  return {
    run_id: run.run_id,
    display_name: run.display_name,
    comment: run.comment,
    parent_run_id: run.parent_run_id,
    code_commit: run.code_commit,
    project: run.project,
    group: run.group,
    group_name: groupNameOf(run.project, run.group),
    origin: run.origin,
    status: run.status,
    created_at: run.created_at,
    started_at: run.started_at,
    ended_at: run.ended_at,
  };
}

function computeState(): StateResp {
  for (const run of runs.values()) {
    const runningJob = (jobsByRun.get(run.run_id) ?? []).find((j) => j.status === 'running');
    if (runningJob) {
      return { busy: true, active: { run_id: run.run_id, job_id: runningJob.job_id, kind: runningJob.kind, origin: run.origin } };
    }
  }
  for (const run of runs.values()) {
    if (run.origin === 'cli' && run.status === 'running') {
      return { busy: true, active: { run_id: run.run_id, job_id: null, kind: 'train', origin: 'cli' } };
    }
  }
  return { busy: false, active: null };
}

function buildTree(): ProjectNode[] {
  const projects = new Map<string, Map<string, RunSummary[]>>();
  for (const p of emptyProjects) {
    if (!projects.has(p)) projects.set(p, new Map());
  }
  for (const run of runs.values()) {
    if (!projects.has(run.project)) projects.set(run.project, new Map());
    const groups = projects.get(run.project)!;
    if (!groups.has(run.group)) groups.set(run.group, []);
    groups.get(run.group)!.push(toSummary(run));
  }
  return Array.from(projects.keys())
    .sort()
    .map((project) => {
      const groupMap = projects.get(project)!;
      const groups: GroupNode[] = Array.from(groupMap.keys())
        .sort((a, b) => groupNameOf(project, a).localeCompare(groupNameOf(project, b)))
        .map((g) => ({
          group: g,
          group_name: groupNameOf(project, g),
          runs: groupMap.get(g)!.slice().sort((a, b) => b.created_at.localeCompare(a.created_at)),
        }));
      return { project, workspace: 'td_plan', stage: project.split('/')[0] === 'src' ? '本番' : 'scratch', groups };
    });
}

// ---- 初期データ ----
function seed() {
  const t1 = makeRun({
    run_id: '20260918_101533',
    project: 'src/world_model_v1',
    group: 'wm_v1_d128_L6',
    origin: 'webui',
    status: 'finished',
    created_at: hoursAgo(72).toISOString(),
    started_at: hoursAgo(72).toISOString(),
    ended_at: hoursAgo(71).toISOString(),
    config_toml: setGroupInToml(SAMPLE_CONFIG, 'wm_v1_d128_L6'),
    git_commit: 'abcdef1',
    git_diff: null,
    git_untracked: null,
    git_info_source: 'sync',
    hostname: 'gpu-host',
    pid: 12345,
    last_heartbeat_at: null,
    exit_code: 0,
    error: null,
  });
  addJob(t1.run_id, {
    job_id: generateJobId(t1.run_id, 'train'),
    run_id: t1.run_id,
    kind: 'train',
    status: 'finished',
    started_at: t1.created_at,
    ended_at: t1.ended_at as string,
    exit_code: 0,
    error: null,
  });
  for (let i = 0; i <= 40; i += 1) {
    const step = i * 20;
    const loss = 1.0 * Math.exp(-i / 12) + 0.02;
    pushMetric(t1.run_id, 'train/loss', step, Number(loss.toFixed(4)), 'train', hoursAgo(72 - i * 0.2).toISOString());
  }
  pushImage(t1.run_id, 'train/sample', 0, dummyImage('train/sample', 0, '#2563eb'), 'train', hoursAgo(72).toISOString());
  definePlot(t1.run_id, {
    label: 'infer/curve', kind: 'line', title: '推論カーブ', xlabel: 't', ylabel: 'value',
    xscale: 'linear', yscale: 'linear', xlim: null, ylim: null,
  });
  const ts = Array.from({ length: 200 }, (_, i) => i / 20);
  pushPlotPoints(t1.run_id, 'infer/curve', 'pred', ts, ts.map((t) => Math.sin(t) + Math.random() * 0.1));
  pushPlotPoints(t1.run_id, 'infer/curve', 'target', ts, ts.map((t) => Math.sin(t)));
  definePlot(t1.run_id, {
    label: 'infer/scatter', kind: 'scatter', title: null, xlabel: 'x', ylabel: 'y',
    xscale: 'linear', yscale: 'log', xlim: null, ylim: null,
  });
  // 間引き表示の確認用に上限（1000点）を超える点を入れる
  const xs = Array.from({ length: 3000 }, () => Math.random() * 10);
  pushPlotPoints(t1.run_id, 'infer/scatter', 'default', xs, xs.map((x) => Math.exp(-x / 3) + Math.random() * 0.05));
  pushImage(t1.run_id, 'train/sample', 800, dummyImage('train/sample', 800, '#2563eb'), 'train', hoursAgo(71.2).toISOString());
  const inferJobId = generateJobId(t1.run_id, 'infer');
  addJob(t1.run_id, {
    job_id: inferJobId,
    run_id: t1.run_id,
    kind: 'infer',
    status: 'finished',
    started_at: hoursAgo(10).toISOString(),
    ended_at: hoursAgo(10).toISOString(),
    exit_code: 0,
    error: null,
  });
  logsByJob.set(inferJobId, `[${hoursAgo(10).toISOString()}] infer開始 (mock)\n[${hoursAgo(10).toISOString()}] infer完了\n`);
  pushTable(t1.run_id, 'infer/summary', '全滅率', 0.62, 'infer', hoursAgo(10).toISOString());
  pushTable(t1.run_id, 'infer/summary', '撃破時刻の一致', 0.99, 'infer', hoursAgo(10).toISOString());
  pushImage(t1.run_id, 'infer/xy', null, dummyImage('infer/xy', null, '#16a34a'), 'infer', hoursAgo(10).toISOString());

  const t2 = makeRun({
    run_id: '20260920_090000',
    project: 'src/world_model_v1',
    group: 'wm_v1_d128_L6',
    origin: 'webui',
    status: 'failed',
    created_at: hoursAgo(48).toISOString(),
    started_at: hoursAgo(48).toISOString(),
    ended_at: hoursAgo(47.9).toISOString(),
    config_toml: setGroupInToml(SAMPLE_CONFIG, 'wm_v1_d128_L6'),
    git_commit: 'abcdef2',
    git_diff: null,
    git_untracked: null,
    git_info_source: 'sync',
    hostname: 'gpu-host',
    pid: 12346,
    last_heartbeat_at: null,
    exit_code: 1,
    error: 'Traceback (most recent call last):\n  ...\nRuntimeError: CUDA out of memory',
  });
  const failedJobId = generateJobId(t2.run_id, 'train');
  addJob(t2.run_id, {
    job_id: failedJobId,
    run_id: t2.run_id,
    kind: 'train',
    status: 'failed',
    started_at: t2.created_at,
    ended_at: t2.ended_at as string,
    exit_code: 1,
    error: t2.error,
  });
  logsByJob.set(failedJobId, `[${t2.created_at}] train開始 (mock)\n[${t2.ended_at}] RuntimeError: CUDA out of memory\n`);

  makeRun({
    run_id: '20260923_120000',
    project: 'src/world_model_v1',
    group: 'wm_v1_d256_L8',
    origin: 'webui',
    status: 'draft',
    created_at: hoursAgo(24).toISOString(),
    started_at: null,
    ended_at: null,
    config_toml: setGroupInToml(SAMPLE_CONFIG, 'wm_v1_d256_L8'),
    git_commit: null,
    git_diff: null,
    git_untracked: null,
    git_info_source: null,
    hostname: null,
    pid: null,
    last_heartbeat_at: null,
    exit_code: null,
    error: null,
  });

  const t4 = makeRun({
    run_id: '20260915_083000',
    project: 'src/world_model_v2',
    group: 'wm_v2_base',
    origin: 'webui',
    status: 'stopped',
    created_at: hoursAgo(96).toISOString(),
    started_at: hoursAgo(96).toISOString(),
    ended_at: hoursAgo(95.5).toISOString(),
    config_toml: setGroupInToml(SAMPLE_CONFIG, 'wm_v2_base'),
    git_commit: 'abcdef3',
    git_diff: null,
    git_untracked: null,
    git_info_source: 'sync',
    hostname: 'gpu-host',
    pid: 12347,
    last_heartbeat_at: null,
    exit_code: null,
    error: null,
  });
  addJob(t4.run_id, {
    job_id: generateJobId(t4.run_id, 'train'),
    run_id: t4.run_id,
    kind: 'train',
    status: 'stopped',
    started_at: t4.created_at,
    ended_at: t4.ended_at as string,
    exit_code: null,
    error: null,
  });
  for (let i = 0; i <= 10; i += 1) {
    pushMetric(t4.run_id, 'train/loss', i * 20, Number((0.8 - i * 0.02).toFixed(4)), 'train', hoursAgo(96 - i * 0.2).toISOString());
  }

  const t5 = makeRun({
    run_id: '20260924_200000',
    project: 'src/world_model_v2',
    group: 'wm_v2_base',
    origin: 'cli',
    status: 'lost',
    created_at: hoursAgo(6).toISOString(),
    started_at: hoursAgo(6).toISOString(),
    ended_at: null,
    config_toml: setGroupInToml(SAMPLE_CONFIG, 'wm_v2_base'),
    git_commit: 'abcdef4',
    git_diff: '--- a\n+++ b\n',
    git_untracked: '{}',
    git_info_source: 'last_sync',
    hostname: 'gpu-host',
    pid: 22222,
    last_heartbeat_at: hoursAgo(1).toISOString(),
    exit_code: null,
    error: null,
  });
  for (let i = 0; i <= 15; i += 1) {
    pushMetric(t5.run_id, 'train/loss', i * 20, Number((0.9 - i * 0.03).toFixed(4)), 'train', hoursAgo(6 - i * 0.3).toISOString());
  }

  const t6 = makeRun({
    run_id: '20260910_070000',
    project: 'src/world_model_v2',
    group: 'wm_v2_base',
    origin: 'cli',
    status: 'finished',
    created_at: hoursAgo(150).toISOString(),
    started_at: hoursAgo(150).toISOString(),
    ended_at: hoursAgo(148).toISOString(),
    config_toml: setGroupInToml(SAMPLE_CONFIG, 'wm_v2_base'),
    git_commit: 'abcdef5',
    git_diff: null,
    git_untracked: null,
    git_info_source: 'last_sync',
    hostname: 'gpu-host',
    pid: 22223,
    last_heartbeat_at: hoursAgo(148).toISOString(),
    exit_code: 0,
    error: null,
  });
  for (let i = 0; i <= 20; i += 1) {
    pushMetric(t6.run_id, 'train/loss', i * 20, Number((1.0 * Math.exp(-i / 10) + 0.03).toFixed(4)), 'train', hoursAgo(150 - i * 0.1).toISOString());
  }

  const running = makeRun({
    run_id: generateRunId(),
    project: 'src/world_model_v1',
    group: 'wm_v1_d128_L6',
    origin: 'webui',
    status: 'running',
    created_at: isoNow(),
    started_at: isoNow(),
    ended_at: null,
    config_toml: setGroupInToml(SAMPLE_CONFIG, 'wm_v1_d128_L6'),
    git_commit: 'abcdef6',
    git_diff: null,
    git_untracked: null,
    git_info_source: 'sync',
    hostname: 'gpu-host',
    pid: 33333,
    last_heartbeat_at: null,
    exit_code: null,
    error: null,
  });
  const runningJobId = generateJobId(running.run_id, 'train');
  addJob(running.run_id, {
    job_id: runningJobId,
    run_id: running.run_id,
    kind: 'train',
    status: 'running',
    started_at: isoNow(),
    ended_at: null,
    exit_code: null,
    error: null,
  });
  logsByJob.set(runningJobId, `[${isoNow()}] train開始 (mock)\n`);

  emptyProjects.push('scratch/PolylineInterp');
}

seed();

// ---- 実行中ジョブのシミュレーション（1秒ごと） ----
function tick() {
  const now = isoNow();
  for (const run of runs.values()) {
    const jobList = jobsByRun.get(run.run_id) ?? [];
    for (const job of jobList) {
      if (job.status !== 'running') continue;
      const ticks = (tickCounts.get(job.job_id) ?? 0) + 1;
      tickCounts.set(job.job_id, ticks);
      appendLog(job.job_id, `[${now}] step ${ticks}: ${job.kind}実行中...\n`);
      if (job.kind === 'train') {
        const step = ticks * 10;
        const loss = Math.max(0.02, 1.0 * Math.exp(-ticks / 8) + Math.random() * 0.02);
        pushMetric(run.run_id, 'train/loss', step, Number(loss.toFixed(4)), 'train', now);
        pushMetric(run.run_id, 'train/lr', step, 0.0003, 'train', now);
        if (!plotsByRun.get(run.run_id)?.has('train/xy')) {
          definePlot(run.run_id, {
            label: 'train/xy', kind: 'line', title: null, xlabel: 'x', ylabel: 'y',
            xscale: 'linear', yscale: 'linear', xlim: null, ylim: null,
          });
        }
        pushPlotPoints(run.run_id, 'train/xy', 'default', [Math.cos(ticks / 3) * ticks], [Math.sin(ticks / 3) * ticks]);
        if (ticks % 5 === 0) {
          pushImage(run.run_id, 'train/sample', step, dummyImage('train/sample', step, '#2563eb'), 'train', now);
        }
      } else if (job.kind === 'infer') {
        if (ticks % 3 === 0) {
          pushImage(run.run_id, 'infer/xy', null, dummyImage('infer/xy', null, '#16a34a'), 'infer', now);
        }
      }
      const finishAt = job.kind === 'train' ? 18 : 6;
      if (ticks >= finishAt) {
        job.status = 'finished';
        job.ended_at = now;
        job.exit_code = 0;
        appendLog(job.job_id, `[${now}] ジョブ終了 (exit=0)\n`);
        if (job.kind === 'train') {
          run.status = 'finished';
          run.ended_at = now;
          run.exit_code = 0;
        }
      }
    }
  }
}

if (typeof window !== 'undefined') {
  window.setInterval(tick, 1000);
}

// ---- Api実装 ----
async function getTree(): Promise<ProjectNode[]> {
  return buildTree();
}

async function getState(): Promise<StateResp> {
  return computeState();
}

async function createDraftFrom(project: string, kind: 'continue' | 'rerun', runId: string): Promise<Run> {
  const src = runs.get(runId);
  if (!src) throw new Error('元の run が見つかりません');
  if (kind === 'rerun' && !src.code_commit) throw new Error('この run はコードを記録していないため、同じコードでは再実行できません');
  const draft = makeRun({
    run_id: generateRunId(), project, group: src.group, origin: 'webui', status: 'draft', created_at: isoNow(),
    started_at: null, ended_at: null, config_toml: src.config_toml, git_commit: null, git_diff: null,
    git_untracked: null, git_info_source: null, hostname: null, pid: null, last_heartbeat_at: null,
    exit_code: null, error: null,
  });
  draft.parent_run_id = kind === 'continue' ? runId : null;
  draft.code_commit = kind === 'rerun' ? src.code_commit : null;
  runs.set(draft.run_id, draft);
  return cloneRun(draft);
}

async function createDraft(project: string, groupArg: string | null, newGroup: boolean): Promise<Run> {
  const runId = generateRunId();
  let group = groupArg ?? '';
  if (newGroup) {
    // backend と同じく group_id は日時から、group_name は project 内の連番で自動で振る
    group = `g${runId}`;
    const used = new Set(Array.from(runs.values()).filter((r) => r.project === project).map((r) => groupNameOf(project, r.group)));
    let n = 1;
    while (used.has(`group${n}`)) n += 1;
    groupNames.set(groupKeyOf(project, group), `group${n}`);
  }
  const existingInGroup = Array.from(runs.values()).filter((r) => r.project === project && r.group === group && r.status !== 'draft');
  const existingInProject = Array.from(runs.values()).filter((r) => r.project === project && r.status !== 'draft');
  let baseConfig = SAMPLE_CONFIG;
  if (!newGroup && existingInGroup.length > 0) {
    baseConfig = latestByCreated(existingInGroup).config_toml;
  } else if (existingInProject.length > 0) {
    baseConfig = latestByCreated(existingInProject).config_toml;
  }
  const run = makeRun({
    run_id: runId,
    project,
    group,
    origin: 'webui',
    status: 'draft',
    created_at: isoNow(),
    started_at: null,
    ended_at: null,
    config_toml: setGroupInToml(baseConfig, group),
    git_commit: null,
    git_diff: null,
    git_untracked: null,
    git_info_source: null,
    hostname: null,
    pid: null,
    last_heartbeat_at: null,
    exit_code: null,
    error: null,
  });
  return cloneRun(run);
}

async function getRun(runId: string): Promise<Run> {
  return cloneRun(getOrThrow(runId));
}

async function updateConfig(runId: string, configToml: string): Promise<Run> {
  const run = getOrThrow(runId);
  if (run.status !== 'draft') {
    throw new Error('draft以外のrunのconfigは編集できません');
  }
  const group = extractGroup(configToml);
  if (group === null) {
    throw new Error('TOMLとして不正です（[wandb].group が見つかりません）');
  }
  run.config_toml = configToml;
  if (group !== run.group) run.display_name = nextDisplayName(run.project, group);
  run.group = group;
  return cloneRun(run);
}

async function startRun(runId: string): Promise<Run> {
  const run = getOrThrow(runId);
  if (run.status !== 'draft') {
    throw new Error('draft以外のrunは開始できません');
  }
  if (computeState().busy) {
    throw new Error('他のジョブが実行中です（GPU使用中のため開始できません）');
  }
  const now = isoNow();
  run.status = 'running';
  run.started_at = now;
  const jobId = generateJobId(run.run_id, 'train');
  addJob(run.run_id, { job_id: jobId, run_id: run.run_id, kind: 'train', status: 'running', started_at: now, ended_at: null, exit_code: null, error: null });
  logsByJob.set(jobId, `[${now}] train開始 (mock)\n`);
  return cloneRun(run);
}

async function stopRun(runId: string, _graceful = false): Promise<Run> {
  const run = getOrThrow(runId);
  const runningJob = (jobsByRun.get(runId) ?? []).find((j) => j.status === 'running');
  if (!runningJob) {
    throw new Error('実行中のジョブがありません');
  }
  const now = isoNow();
  runningJob.status = 'stopped';
  runningJob.ended_at = now;
  appendLog(runningJob.job_id, `[${now}] 中止されました\n`);
  if (runningJob.kind === 'train') {
    run.status = 'stopped';
    run.ended_at = now;
  }
  return cloneRun(run);
}

async function deleteRun(runId: string): Promise<void> {
  const run = getOrThrow(runId);
  if (run.status === 'running') {
    throw new Error('runningのrunは削除できません');
  }
  runs.delete(runId);
  jobsByRun.delete(runId);
  resultsByRun.delete(runId);
  plotsByRun.delete(runId);
}

async function runInfer(project: string, runId: string): Promise<Job> {
  const run = runs.get(runId);
  if (!run || run.project !== project) throw new Error('対象のrunが見つかりません');
  return groupInfer(project, run.group);
}

async function groupInfer(project: string, group: string): Promise<Job> {
  if (computeState().busy) {
    throw new Error('他のジョブが実行中です（GPU使用中のため開始できません）');
  }
  const candidates = Array.from(runs.values()).filter((r) => r.project === project && r.group === group && r.status !== 'draft');
  if (candidates.length === 0) {
    throw new Error('対象のrun（学習済みcheckpoint）が見つかりません');
  }
  const target = latestByCreated(candidates);
  const now = isoNow();
  const jobId = generateJobId(target.run_id, 'infer');
  const job: Job = { job_id: jobId, run_id: target.run_id, kind: 'infer', status: 'running', started_at: now, ended_at: null, exit_code: null, error: null };
  addJob(target.run_id, job);
  logsByJob.set(jobId, `[${now}] infer開始 (mock)\n`);
  return job;
}

async function getJobLog(jobId: string, offset: number): Promise<JobLogResp> {
  const full = logsByJob.get(jobId) ?? '';
  const data = full.slice(offset);
  const job = findJob(jobId);
  const done = !job || job.status !== 'running';
  return { data, offset: full.length, done };
}

async function getResults(
  runId: string, kind?: 'metric' | 'image' | 'table', label?: string, opts?: { afterId?: number | null },
): Promise<Result[]> {
  const list = resultsByRun.get(runId) ?? [];
  return list
    .filter((r) => (!kind || r.kind === kind) && (!label || r.label === label) && (opts?.afterId == null || r.id > opts.afterId))
    .slice()
    .sort((a, b) => a.sent_at.localeCompare(b.sent_at));
}

async function getLabels(runIds: string[]): Promise<LabelInfo[]> {
  const map = new Map<string, LabelInfo['kind']>();
  for (const id of runIds) {
    for (const r of resultsByRun.get(id) ?? []) {
      map.set(r.label, r.kind);
    }
    for (const label of plotsByRun.get(id)?.keys() ?? []) {
      map.set(label, 'plot');
    }
  }
  return Array.from(map.entries())
    .map(([label, kind]) => ({ label, kind }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

// backend と同じく、上限を超えた系列は間引き済みの全体を返す（モックでは等間隔に間引くだけ）
async function getPlots(
  runId: string, afterId?: number | null, opts?: { omitStepLines?: boolean; label?: string },
): Promise<Plot[]> {
  const byLabel = plotsByRun.get(runId);
  if (!byLabel) return [];
  return Array.from(byLabel.values())
    .filter(({ def }) => !opts?.label || def.label === opts.label)
    .map(({ def, points }) => {
      const names = Array.from(new Set(points.map((p) => p.series)));
      const series = names.map((name) => {
        const all = points.filter((p) => p.series === name);
        const downsampled = all.length > MOCK_MAX_POINTS;
        const picked = downsampled
          ? Array.from({ length: MOCK_MAX_POINTS }, (_, i) => all[Math.floor((i * all.length) / MOCK_MAX_POINTS)])
          : all.filter((p) => afterId == null || p.id > afterId);
        return {
          name,
          points: picked.map((p) => [p.x, p.y] as [number, number]),
          total_points: all.length,
          downsampled,
        };
      });
      const lastId = points.length > 0 ? points[points.length - 1].id : null;
      return { ...def, series, last_point_id: lastId };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
}

async function getBinnedPlot(runId: string, label: string, bin: string): Promise<BinnedPlot> {
  const entry = plotsByRun.get(runId)?.get(label);
  if (!entry) throw new Error('グラフが見つかりません');
  const epoch = bin === 'epoch';
  const width = epoch ? 100 : Number(bin);
  if (!(width > 0)) throw new Error('bin は "epoch" か正の数にしてください');
  const names = Array.from(new Set(entry.points.map((p) => p.series)));
  const series = names.map((name) => {
    const sums = new Map<number, [number, number]>();
    entry.points.filter((p) => p.series === name).forEach((p) => {
      const k = Math.floor(p.x / width);
      const acc = sums.get(k) ?? [0, 0];
      sums.set(k, [acc[0] + p.y, acc[1] + 1]);
    });
    const points = Array.from(sums.keys()).sort((a, b) => a - b).map((k) => {
      const [s, n] = sums.get(k)!;
      return [epoch ? k + 1 : (k + 1) * width, s / n] as [number, number];
    });
    return { name, points, total_points: entry.points.filter((p) => p.series === name).length };
  });
  return { label, bin, width, epoch, series };
}

async function updateComment(runId: string, comment: string): Promise<Run> {
  const run = getOrThrow(runId);
  run.comment = comment;
  return cloneRun(run);
}

async function updateGroupDescription(project: string, group: string, description: string): Promise<void> {
  groupDescriptions.set(groupKeyOf(project, group), description);
}

async function renameGroup(project: string, group: string, groupName: string): Promise<void> {
  groupNames.set(groupKeyOf(project, group), groupName.trim());
}

const mockComparisons: Comparison[] = [];

export const mockApi: Api = {
  listComparisons: async () => mockComparisons.map((c) => ({ ...c })),
  createComparison: async (name, body) => {
    const now = new Date().toISOString();
    const c: Comparison = { id: `c${mockComparisons.length + 1}`, name, ...body, created_at: now, updated_at: now };
    mockComparisons.push(c);
    return { ...c };
  },
  updateComparison: async (id, patch) => {
    const c = mockComparisons.find((x) => x.id === id);
    if (!c) throw new Error('比較が見つかりません');
    Object.assign(c, patch, { updated_at: new Date().toISOString() });
    return { ...c };
  },
  deleteComparison: async (id) => {
    const i = mockComparisons.findIndex((x) => x.id === id);
    if (i >= 0) mockComparisons.splice(i, 1);
  },
  getTree,
  rescan: getTree,
  getState,
  createDraft,
  createDraftFrom,
  renameGroup,
  updateComment,
  updateGroupDescription,
  getRun,
  updateConfig,
  startRun,
  stopRun,
  deleteRun,
  groupInfer,
  runInfer,
  getJobLog,
  getResults,
  getLabels,
  getMachines: async () => ({
    default: 'gpu1',
    selected: 'gpu1',
    machines: [
      { name: 'gpu1', priority: 100, reachable: true, own_jobs: 0, other_mib: 0, free_mib: 30000, total_mib: 32000, available: true, reason: null, group: ['gpu1', 'gpu5'] },
      { name: 'gpu5', priority: 60, reachable: true, own_jobs: 0, other_mib: 9000, free_mib: 3000, total_mib: 12000, available: false, reason: '他の人が使用中です（9000 MiB）', group: ['gpu1', 'gpu5'] },
    ],
  }),
  getMonitor: async () => ({
    cpu_total: 40,
    cpu_cores: Array.from({ length: 16 }, () => Math.round(Math.random() * 1000) / 10),
    gpus: [{ index: 0, name: 'NVIDIA GeForce GTX 1080', util: Math.round(Math.random() * 100), mem_used_mib: 5000,
             mem_total_mib: 8192, temp_c: 70, power_w: 150 }],
  }),
  getPlots,
  getBinnedPlot,
  putView: async () => {},
};
