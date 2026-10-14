import { api } from '../api';
import type { MonitorResp } from '../api';
import { usePolling } from '../hooks/usePolling';

const INTERVAL_MS = 2000;

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

function Gpus({ data }: { data: MonitorResp }) {
  if (data.gpus.length === 0) return <p className="mon-note">GPU が見つかりません（nvidia-smi が使えません）。</p>;
  return (
    <>
      {data.gpus.map((g) => {
        const memPct = g.mem_used_mib !== null && g.mem_total_mib ? (100 * g.mem_used_mib) / g.mem_total_mib : null;
        const extra = [g.temp_c !== null ? `${g.temp_c.toFixed(0)}℃` : null, g.power_w !== null ? `${g.power_w.toFixed(0)}W` : null]
          .filter(Boolean)
          .join(' / ');
        return (
          <div key={g.index} className="mon-gpu">
            <div className="mon-gpu-name">GPU {g.index}: {g.name}{extra && `（${extra}）`}</div>
            <Bar value={g.util} label="使用率" />
            <Bar
              value={memPct}
              label="メモリ"
              detail={g.mem_used_mib !== null && g.mem_total_mib !== null ? `${g.mem_used_mib.toFixed(0)} / ${g.mem_total_mib.toFixed(0)} MiB` : undefined}
            />
          </div>
        );
      })}
    </>
  );
}

// 学習用PCの GPU・CPU（コアごと）の使用率。このパネルを開いている間だけ（表示している間だけ）2秒ごとに取りに行く
// （マウントされている間のポーリングなので、別のタブに切り替えれば止まる。タブが裏にある間も止まる）
export function MonitorPanel() {
  const { data, error } = usePolling(() => api.getMonitor(), INTERVAL_MS, []);
  return (
    <div className="mon-panel">
      <h2>学習用PCの使用状況</h2>
      {error && <div className="error-banner">{error}</div>}
      {!data && !error && <p className="mon-note">取得中…</p>}
      {data && (
        <>
          <h3>GPU</h3>
          <Gpus data={data} />
          <h3>CPU（コアごと）{data.cpu_total !== null && `　全体 ${data.cpu_total.toFixed(0)}%`}</h3>
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
