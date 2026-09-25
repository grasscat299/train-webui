import { useMemo, useState } from 'react';
import type { ImageResult } from '../api';

// 1ラベル分の画像: 最新を表示しつつ、sent_at順に過去へ遡れるようにする（design.md §8）。
export function ImageViewer({ images }: { images: ImageResult[] }) {
  const sorted = useMemo(() => images.slice().sort((a, b) => b.sent_at.localeCompare(a.sent_at)), [images]);
  const [index, setIndex] = useState(0);
  if (sorted.length === 0) return <div className="no-image">画像なし</div>;
  const clamped = Math.min(index, sorted.length - 1);
  const current = sorted[clamped];
  return (
    <div className="image-viewer">
      <a href={current.image_url} target="_blank" rel="noreferrer" title="クリックで元の大きさで開く">
        <img src={current.image_url} alt={current.label} />
      </a>
      <div className="image-viewer-controls">
        <button disabled={clamped >= sorted.length - 1} onClick={() => setIndex(clamped + 1)}>
          ◀ 過去へ
        </button>
        <span>
          {clamped + 1} / {sorted.length}（{current.sent_at}）
        </span>
        <button disabled={clamped <= 0} onClick={() => setIndex(clamped - 1)}>
          最新へ ▶
        </button>
      </div>
    </div>
  );
}
