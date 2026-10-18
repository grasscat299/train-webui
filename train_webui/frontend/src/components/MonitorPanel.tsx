import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import type { LabHistoryRow, LabNow } from '../api';
import { usePolling } from '../hooks/usePolling';

// Monitor タブ。gpu5 の lab_monitor server（GET /api/lab/now, /api/lab/history）の値を、lab_monitor の画面と同じ見た目で出す。
// 色・グラフの描き方は lab_monitor/static/index.html と同じ（色は styles.css の .labmon で明暗とも定義）。
const NOW_INTERVAL_MS = 5000;
const HISTORY_INTERVAL_MS = 60000;
const RANGES = [{ h: 1, label: '1時間' }, { h: 6, label: '6時間' }, { h: 24, label: '24時間' }, { h: 168, label: '7日' }];

const pct = (v: number, t: number) => (t ? Math.min(100, (100 * v) / t) : 0);
const fmtG = (mib: number) => `${(mib / 1024).toFixed(1)} GB`;

type Series = [(r: LabHistoryRow) => number | null | undefined, string][];

// 0〜100 の縦軸（% と ℃ 共通）と時刻の横軸つきの折れ線。取得に失敗した点（ok=0）の所は線を切る
function drawChart(cv: HTMLCanvasElement, rows: LabHistoryRow[], hours: number, series: Series) {
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  cv.width = W * dpr;
  cv.height = H * dpr;
  const x = cv.getContext('2d');
  if (!x) return;
  x.scale(dpr, dpr);
  const st = getComputedStyle(cv);
  const css = (n: string) => st.getPropertyValue(n).trim();
  const mut = css('--lm-mut'), grid = css('--lm-grid');
  const L = 34, R = 8, T = 6, B = 18, pw = W - L - R, ph = H - T - B;
  const Y = (v: number) => T + ph * (1 - Math.min(100, Math.max(0, v)) / 100);
  const t1 = Date.now() / 1000, t0 = t1 - hours * 3600;
  const X = (t: number) => L + ((t - t0) / (t1 - t0)) * pw;
  x.font = '11px system-ui,sans-serif';
  x.lineWidth = 1;
  x.textAlign = 'right';
  x.textBaseline = 'middle';
  for (const v of [0, 25, 50, 75, 100]) {
    x.strokeStyle = grid;
    x.beginPath(); x.moveTo(L, Y(v)); x.lineTo(W - R, Y(v)); x.stroke();
    if (v % 50 === 0) { x.fillStyle = mut; x.fillText(String(v), L - 5, Y(v)); }
  }
  // 横軸は範囲ごとに間隔を変え、ローカル時刻のキリのよい所に目盛りを置く
  const step = hours <= 1 ? 600 : hours <= 6 ? 3600 : hours <= 24 ? 4 * 3600 : 86400;
  const off = new Date().getTimezoneOffset() * 60;
  x.textAlign = 'center';
  x.textBaseline = 'top';
  let lastX = -1e9;
  for (let t = Math.ceil((t0 - off) / step) * step + off; t <= t1; t += step) {
    const px = X(t);
    x.strokeStyle = grid;
    x.beginPath(); x.moveTo(px, T); x.lineTo(px, T + ph); x.stroke();
    const d = new Date(t * 1000);
    const lab = step >= 86400 ? `${d.getMonth() + 1}/${d.getDate()}` : `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    if (px - lastX > 38 && px < W - R - 10) { x.fillStyle = mut; x.fillText(lab, px, T + ph + 4); lastX = px; }
  }
  if (!rows.length) return;
  x.save();
  x.beginPath(); x.rect(L, T - 2, pw, ph + 4); x.clip();
  for (const [f, c] of series) {
    x.strokeStyle = css(c);
    x.lineWidth = 2;
    x.lineJoin = 'round';
    x.beginPath();
    let pen = false;
    for (const r of rows) {
      const v = f(r);
      if (!r.ok || v == null) { pen = false; continue; }
      if (pen) x.lineTo(X(r.ts), Y(v)); else x.moveTo(X(r.ts), Y(v));
      pen = true;
    }
    x.stroke();
  }
  x.restore();
}

function Chart({ rows, hours, series }: { rows: LabHistoryRow[]; hours: number; series: Series }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => { if (ref.current) drawChart(ref.current, rows, hours, series); });
  return <canvas ref={ref} className="lm-canvas" />;
}

function Legend({ items, right }: { items: [string, string][]; right: string }) {
  return (
    <div className="lm-row">
      <span>
        {items.map(([name, color]) => (
          <span key={name} className="lm-lg"><i className="lm-k" style={{ background: `var(${color})` }} />{name}</span>
        ))}
      </span>
      <span>{right}　縦軸 % / ℃</span>
    </div>
  );
}

function Bar({ label, value, text, width }: { label: string; value?: string; text: string; width: number }) {
  return (
    <>
      <div className="lm-row"><span>{label}</span><b>{value ?? text}</b></div>
      <div className="lm-bar"><i style={{ width: `${width}%` }} /></div>
    </>
  );
}

function Card({ m, hours, rows }: { m: LabNow; hours: number; rows: LabHistoryRow[] }) {
  const off = m.error !== undefined;
  const noGpu = !off && m.gpu_util == null;
  const badge = off ? ['off', '接続不可'] : noGpu ? ['off', 'GPU不明'] : m.in_use ? ['busy', '使用中'] : ['free', '空き'];
  const cpu = m.cpu ?? 0;
  return (
    <div className="lm-card">
      <div className="lm-top">
        <div><span className="lm-name">{m.title || m.name}</span> <span className="lm-lab">{m.label}</span></div>
        <span className={`lm-badge ${badge[0]}`}>{badge[1]}</span>
      </div>
      {off ? (
        <p className="lm-err">{m.error}</p>
      ) : (
        <>
          <Bar label="GPU" text={noGpu ? '取得できません' : `${(m.gpu_util ?? 0).toFixed(0)} %`} width={noGpu ? 0 : (m.gpu_util ?? 0)} />
          <Bar label="VRAM" text={noGpu ? '—' : `${fmtG(m.vram_used ?? 0)} / ${fmtG(m.vram_total ?? 0)}`} width={noGpu ? 0 : pct(m.vram_used ?? 0, m.vram_total ?? 0)} />
          <Bar label="CPU/(RAM)" text={`${cpu.toFixed(0)} % / (${fmtG(m.ram_used ?? 0)} / ${fmtG(m.ram_total ?? 0)})`} width={cpu} />
        </>
      )}
      <Chart rows={rows} hours={hours} series={[[(r) => r.gpu_util, '--lm-s1'], [(r) => (r.vram_total ? pct(r.vram_used ?? 0, r.vram_total) : null), '--lm-s3'], [(r) => r.temp, '--lm-s2']]} />
      <Legend items={[['GPU使用率', '--lm-s1'], ['VRAM', '--lm-s3'], ['GPU温度', '--lm-s2']]} right={m.temp != null ? `GPU ${m.temp.toFixed(0)} ℃` : ''} />
      <Chart rows={rows} hours={hours} series={[[(r) => r.cpu, '--lm-s1'], [(r) => r.cpu_temp, '--lm-s2']]} />
      <Legend items={[['CPU使用率', '--lm-s1'], ['CPU温度', '--lm-s2']]} right={m.cpu_temp != null ? `CPU ${m.cpu_temp.toFixed(0)} ℃` : ''} />
    </div>
  );
}

export function MonitorPanel() {
  const [hours, setHours] = useState(1);
  const { data, error } = usePolling(() => api.getLabNow(), NOW_INTERVAL_MS, []);
  const [hist, setHist] = useState<Record<string, LabHistoryRow[]>>({});
  const names = (data ?? []).map((m) => m.name).join(',');
  // 履歴は台ごとに、範囲を変えた時と 1 分ごとに取り直す
  const { data: histData } = usePolling(
    async () => Object.fromEntries(await Promise.all(names.split(',').filter(Boolean).map(async (n) => [n, await api.getLabHistory(n, hours)] as const))),
    HISTORY_INTERVAL_MS,
    [names, hours],
  );
  useEffect(() => { if (histData) setHist(histData); }, [histData]);
  return (
    <div className="mon-panel labmon">
      <div className="lm-head">
        <h2>学習用 PC の使用状況</h2>
        <label className="lm-sub">履歴 <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          {RANGES.map((r) => <option key={r.h} value={r.h}>{r.label}</option>)}
        </select></label>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {!data && !error && <p className="mon-note">取得中…</p>}
      <div className="lm-grid">
        {(data ?? []).map((m) => <Card key={m.name} m={m} hours={hours} rows={hist[m.name] ?? []} />)}
      </div>
    </div>
  );
}
