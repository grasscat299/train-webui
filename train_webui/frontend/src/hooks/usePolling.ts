import { useEffect, useRef, useState } from 'react';

// 一定間隔でfetcherを呼び続け、結果をstateに反映する汎用フック。
// GET /api/tree, /api/state のような定期ポーリング（design.md/CONTRACT.md §2.2）に使う。
export function usePolling<T>(fetcher: () => Promise<T>, intervalMs: number, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const result = await fetcherRef.current();
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) timer = window.setTimeout(tick, intervalMs);
      }
    };
    tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, setData };
}
