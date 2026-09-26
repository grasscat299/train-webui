import { createContext, useContext, useEffect, useState } from 'react';
import type { CSSProperties } from 'react';

// 画面の大きさの設定（ナビ幅・パネル幅・グラフ高さ・画像幅）。CSS 変数として .app に渡し、各所の CSS がそれを使う。
// 閲覧者ごとの好みなので localStorage に保存する（使えない環境でも既定値で動く）。
export interface ViewSettings {
  navWidth: number; // px
  panelWidth: number; // px（グリッドの1パネルの最小幅）
  chartHeight: number; // px
  imageWidth: number; // px（画像の幅。画像のパネルはこの幅に合わせて伸び縮みする）
}

export const DEFAULT_VIEW: ViewSettings = { navWidth: 264, panelWidth: 380, chartHeight: 220, imageWidth: 720 };

export const VIEW_LIMITS: Record<keyof ViewSettings, { min: number; max: number; step: number }> = {
  navWidth: { min: 180, max: 640, step: 10 },
  panelWidth: { min: 240, max: 1400, step: 20 },
  chartHeight: { min: 140, max: 720, step: 20 },
  imageWidth: { min: 120, max: 2400, step: 20 },
};

const STORAGE_KEY = 'train_webui.view';

function clamp(key: keyof ViewSettings, v: number): number {
  const { min, max } = VIEW_LIMITS[key];
  return Math.min(max, Math.max(min, v));
}

function load(): ViewSettings {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
    const out = { ...DEFAULT_VIEW };
    (Object.keys(DEFAULT_VIEW) as (keyof ViewSettings)[]).forEach((k) => {
      if (typeof raw[k] === 'number') out[k] = clamp(k, raw[k]);
    });
    // 以前は画像の幅を %（100以下）で保存していたので、その値は使わず既定に戻す
    if (typeof raw.imageWidth === 'number' && raw.imageWidth <= 100) out.imageWidth = DEFAULT_VIEW.imageWidth;
    return out;
  } catch {
    return { ...DEFAULT_VIEW };
  }
}

export function useViewSettings() {
  const [view, setView] = useState<ViewSettings>(load);
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(view));
    } catch {
      // 保存できない環境（プライベートモード等）では保存しないだけ
    }
  }, [view]);
  const update = (key: keyof ViewSettings, value: number) => setView((prev) => ({ ...prev, [key]: clamp(key, value) }));
  const reset = () => setView({ ...DEFAULT_VIEW });
  return { view, update, reset };
}

// パネルのドラッグで大きさを変えるため、設定の値と更新関数を画面のどこからでも使えるようにする
export const ViewContext = createContext<{
  view: ViewSettings;
  update: (key: keyof ViewSettings, value: number) => void;
} | null>(null);

export function useView() {
  const ctx = useContext(ViewContext);
  if (!ctx) throw new Error('ViewContext.Provider の外で useView が呼ばれました');
  return ctx;
}

export function viewStyle(view: ViewSettings): CSSProperties {
  return {
    '--nav-width': `${view.navWidth}px`,
    '--panel-width': `${view.panelWidth}px`,
    '--chart-height': `${view.chartHeight}px`,
    '--image-width': `${view.imageWidth}px`,
  } as CSSProperties;
}
