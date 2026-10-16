import { useSyncExternalStore } from 'react';
import type { MouseEvent } from 'react';

// 指標・グラフの説明のツールチップ。ブラウザ標準の title は、画面が自動で更新されると消えて出なくなるので、自前で描く。
// 使い方: 要素に {...tipProps(text)} を付ける（text が無ければ何も付かない）。App に <HoverTipLayer /> を 1 つ置く。
interface TipState { text: string; x: number; y: number }
let state: TipState | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };

export function tipProps(text: string | undefined) {
  if (!text) return {};
  return {
    onMouseEnter: (e: MouseEvent) => { state = { text, x: e.clientX, y: e.clientY }; emit(); },
    onMouseMove: (e: MouseEvent) => { state = { text, x: e.clientX, y: e.clientY }; emit(); },
    onMouseLeave: () => { state = null; emit(); },
  };
}

export function HoverTipLayer() {
  const s = useSyncExternalStore(subscribe, () => state);
  if (!s) return null;
  // 画面の右・下からはみ出さないように寄せる
  const W = 360;
  const left = Math.min(s.x + 14, window.innerWidth - W - 8);
  const top = Math.min(s.y + 18, window.innerHeight - 120);
  return (
    <div className="hover-tip" style={{ left: Math.max(8, left), top: Math.max(8, top), maxWidth: W }}>
      {s.text}
    </div>
  );
}
