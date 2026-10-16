import { useEffect, useState } from 'react';
import { api } from '../api';

// project ごとの指標の説明（名前 → 説明）。同じ project は 1 回だけ取る。取れなければ空（説明が出ないだけ）。
const cache = new Map<string, Promise<Record<string, string>>>();

export function useMetricDocs(project: string | undefined): Record<string, string> {
  const [docs, setDocs] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!project) { setDocs({}); return; }
    let alive = true;
    let p = cache.get(project);
    if (!p) {
      p = api.getMetricDocs(project).catch(() => ({} as Record<string, string>));
      cache.set(project, p);
    }
    p.then((d) => { if (alive) setDocs(d); });
    return () => { alive = false; };
  }, [project]);
  return docs;
}
