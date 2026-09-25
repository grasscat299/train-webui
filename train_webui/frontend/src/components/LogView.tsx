import { useEffect, useRef, useState } from 'react';
import { api } from '../api';

// run.log の差分取得（offset方式、CONTRACT.md §2.2 GET /api/jobs/{job_id}/log）を
// 1.5秒間隔でポーリングし、doneになったら止める。終了済みjobの全文取得にも使える。
export function LogView({ jobId }: { jobId: string }) {
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const preRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let offset = 0;
    setText('');
    const load = async () => {
      try {
        const res = await api.getJobLog(jobId, offset);
        if (cancelled) return;
        if (res.data) setText((prev) => prev + res.data);
        offset = res.offset;
        setError(null);
        if (!res.done) timer = window.setTimeout(load, 1500);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          timer = window.setTimeout(load, 1500);
        }
      }
    };
    load();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [jobId]);

  useEffect(() => {
    if (preRef.current) preRef.current.scrollTop = preRef.current.scrollHeight;
  }, [text]);

  return (
    <div className="log-view-wrap">
      <pre className="log-view" ref={preRef}>
        {text || '(ログなし)'}
      </pre>
      {error && <div className="log-error">ログ取得エラー: {error}</div>}
    </div>
  );
}
