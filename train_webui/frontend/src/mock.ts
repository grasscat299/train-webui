// VITE_MOCK=1 用のモック実装。backendなしで画面の状態遷移を一通り確認できるようにする。
// api.ts の Api インターフェースを満たす（api.ts からは型のみをimportし、循環参照を避ける）。
import type {
  Api,
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
} from './api';

type StoredRun = Omit<Run, 'jobs'>;

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

function makeRun(run: StoredRun): StoredRun {
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

function pushMetric(runId: string, label: string, step: number | null, value: number, source: string, sentAt = isoNow()) {
  const list = resultsByRun.get(runId) ?? [];
  const r: MetricResult = { id: nextResultId++, run_id: runId, kind: 'metric', label, step, value, source, sent_at: sentAt };
  list.push(r);
  resultsByRun.set(runId, list);
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
  return { ...run, jobs: (jobsByRun.get(run.run_id) ?? []).slice() };
}

function toSummary(run: StoredRun): RunSummary {
  return {
    run_id: run.run_id,
    project: run.project,
    group: run.group,
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
        .sort()
        .map((g) => ({
          group: g,
          runs: groupMap.get(g)!.slice().sort((a, b) => b.created_at.localeCompare(a.created_at)),
        }));
      return { project, groups };
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

async function createDraft(project: string, group: string, newGroup: boolean): Promise<Run> {
  const runId = generateRunId();
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

async function stopRun(runId: string): Promise<Run> {
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

async function getResults(runId: string, kind?: 'metric' | 'image', label?: string): Promise<Result[]> {
  const list = resultsByRun.get(runId) ?? [];
  return list
    .filter((r) => (!kind || r.kind === kind) && (!label || r.label === label))
    .slice()
    .sort((a, b) => a.sent_at.localeCompare(b.sent_at));
}

async function getLabels(runIds: string[]): Promise<LabelInfo[]> {
  const map = new Map<string, 'metric' | 'image'>();
  for (const id of runIds) {
    for (const r of resultsByRun.get(id) ?? []) {
      map.set(r.label, r.kind);
    }
  }
  return Array.from(map.entries())
    .map(([label, kind]) => ({ label, kind }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

export const mockApi: Api = {
  getTree,
  rescan: getTree,
  getState,
  createDraft,
  getRun,
  updateConfig,
  startRun,
  stopRun,
  deleteRun,
  groupInfer,
  getJobLog,
  getResults,
  getLabels,
};
