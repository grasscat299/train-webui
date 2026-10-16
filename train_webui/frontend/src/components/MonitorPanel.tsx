import { useEffect, useRef, useState } from 'react';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api } from '../api';
import type { MachineInfo, MachinesResp, MonitorResp } from '../api';
import { usePolling } from '../hooks/usePolling';

const INTERVAL_MS = 2000;
const HISTORY = 150; // グラフに残す点数（2秒ごと = 約5分）

interface GpuPoint { t: number; util: number | null; mem: number | null; temp: number | null }

// 使用率(%)を横棒で出す。高いほど色を変える
function Bar({ value, label, detail }: { value: number | null; label: string; detail?: string }) {
  const v = value ?? 0;
  const level = v >= 90 ? 'high' : v >= 60 ? 'mid' : 'low';
  return (
    <div className="mon-row">
      <span className="mon-label">{label}</span>
      <div className="mon-track">
        <div className={`mon-fill ${level}`} style={{ width: `${Math.min(100, v)}%` }} />
      </div>
      <span className="mon-value">{value === null ? 'N/A' : `${v.toFixed(0)}%`}</span>
      {detail && <span className="mon-detail">{detail}</span>}
    </div>
  );
}

// GPU ごとに、使用率・メモリ(%)・温度の推移を折れ線で出す（温度は右軸）。数値は現在値
function GpuChart({ points }: { points: GpuPoint[] }) {
  return (
    <div className="mon-chart">
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={points} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="t" tickFormatter={(t: number) => `${Math.round(t - points[points.length - 1].t)}s`} fontSize={11} />
          <YAxis yAxisId="pct" domain={[0, 100]} unit="%" width={44} fontSize={11} />
          <YAxis yAxisId="temp" orientation="right" domain={[20, 100]} unit="℃" width={48} fontSize={11} />
          <Tooltip labelFormatter={(t) => `${Math.round(Number(t) - points[points.length - 1].t)} 秒`} formatter={(v) => (v == null ? 'N/A' : Number(v).toFixed(0))} />
          <Legend />
          <Line yAxisId="pct" dataKey="util" name="使用率(%)" stroke="#4c8dff" dot={false} isAnimationActive={false} />
          <Line yAxisId="pct" dataKey="mem" name="メモリ(%)" stroke="#8e6bd8" dot={false} isAnimationActive={false} />
          <Line yAxisId="temp" dataKey="temp" name="温度(℃)" stroke="#d9534f" dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function Gpus({ data, history }: { data: MonitorResp; history: Record<number, GpuPoint[]> }) {
  if (data.gpus.length === 0) return <p className="mon-note">GPU が見つかりません（nvidia-smi が使えません）。</p>;
  return (
    <>
      {data.gpus.map((g) => {
        const extra = [
          g.util !== null ? `使用率 ${g.util.toFixed(0)}%` : null,
          g.mem_used_mib !== null && g.mem_total_mib !== null ? `メモリ ${g.mem_used_mib.toFixed(0)} / ${g.mem_total_mib.toFixed(0)} MiB` : null,
          g.temp_c !== null ? `${g.temp_c.toFixed(0)}℃` : null,
          g.power_w !== null ? `${g.power_w.toFixed(0)}W` : null,
        ].filter(Boolean).join(' / ');
        return (
          <div key={g.index} className="mon-gpu">
            <div className="mon-gpu-name">GPU {g.index}: {g.name}{extra && `（${extra}）`}</div>
            {(history[g.index]?.length ?? 0) > 1 && <GpuChart points={history[g.index]} />}
          </div>
        );
      })}
    </>
  );
}

// 全台の一覧（GET /api/machines。サーバー側で数秒キャッシュされ、不通の台は何度も測り直さない）。行を押すとその台の詳細を下に出す
function Overview({ data, selected, onSelect }: { data: MachinesResp; selected: string; onSelect: (name: string) => void }) {
  const mem = (m: MachineInfo) => {
    if (!m.reachable || m.total_mib === null || m.free_mib === null) return '-';
    return `${m.total_mib - m.free_mib} / ${m.total_mib} MiB`;
  };
  return (
    <table className="mon-overview">
      <thead>
        <tr><th>台</th><th>GPU メモリ</th><th>他の人</th><th>自分のジョブ</th><th>起動</th></tr>
      </thead>
      <tbody>
        {data.machines.map((m) => (
          <tr key={m.name} className={m.name === selected ? 'selected' : ''} onClick={() => onSelect(m.name)} style={{ cursor: 'pointer' }}>
            <td>{m.name}{m.name === data.selected && <span title="いま自動選択される台"> ★</span>}</td>
            <td>{mem(m)}</td>
            <td>{m.reachable ? `${m.other_mib} MiB` : '接続不可'}</td>
            <td>{m.own_jobs}</td>
            <td title={m.reason ?? undefined}>{m.available ? '可' : `不可（${m.reason ?? ''}）`}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// 学習用PCの GPU・CPU（コアごと）の使用率。このパネルを開いている間だけ取りに行く（別のタブに切り替えれば止まる。
// タブが裏にある間も止まる）。一覧は5秒ごと、選んだ台の詳細は2秒ごと。開いていない間は何も測らない
export function MonitorPanel() {
  const [machine, setMachine] = useState('');  // '' = 既定の台
  const { data: machines } = usePolling(() => api.getMachines(), 5000, []);
  const current = machine || machines?.default || '';
  const { data, error } = usePolling(() => api.getMonitor(current || null), INTERVAL_MS, [current]);
  // GPU の推移。台を切り替えたら捨てる（取得のたびに1点足す）
  const [history, setHistory] = useState<Record<number, GpuPoint[]>>({});
  const startRef = useRef(Date.now());
  useEffect(() => setHistory({}), [current]);
  useEffect(() => {
    if (!data) return;
    const t = (Date.now() - startRef.current) / 1000;
    setHistory((h) => {
      const next = { ...h };
      for (const g of data.gpus) {
        const mem = g.mem_used_mib !== null && g.mem_total_mib ? (100 * g.mem_used_mib) / g.mem_total_mib : null;
        next[g.index] = [...(h[g.index] ?? []), { t, util: g.util, mem, temp: g.temp_c }].slice(-HISTORY);
      }
      return next;
    });
  }, [data]);
  return (
    <div className="mon-panel">
      <h2>学習用PCの使用状況</h2>
      {machines && machines.machines.length > 1 && <Overview data={machines} selected={current} onSelect={setMachine} />}
      <h3>{current} の詳細</h3>
      {error && <div className="error-banner">{error}</div>}
      {!data && !error && <p className="mon-note">取得中…</p>}
      {data && (
        <>
          <h3>GPU</h3>
          <Gpus data={data} history={history} />
          <h3>CPU（コアごと）{data.cpu_total !== null && `　全体 ${data.cpu_total.toFixed(0)}%`}{data.cpu_temp_c != null && `　温度 ${data.cpu_temp_c.toFixed(0)}℃`}</h3>
          <div className="mon-cores">
            {data.cpu_cores.map((c, i) => (
              <Bar key={i} value={c} label={`${i}`} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
