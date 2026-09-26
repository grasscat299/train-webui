import { useEffect, useRef, useState } from 'react';
import type { ViewSettings } from '../viewSettings';
import { VIEW_LIMITS } from '../viewSettings';

const ROWS: { key: keyof ViewSettings; label: string; unit: string }[] = [
  { key: 'fontScale', label: '文字の大きさ', unit: '%' },
  { key: 'navWidth', label: 'ナビの幅', unit: 'px' },
  { key: 'panelWidth', label: 'グラフの幅', unit: 'px' },
  { key: 'chartHeight', label: 'グラフの高さ', unit: 'px' },
  { key: 'imageWidth', label: '画像の幅（一括）', unit: 'px' },
];

// 上部バーの「表示設定」: スライダーで大きさを変える。変更はすぐ全パネル・全画像に反映される。
export type Theme = 'dark' | 'light' | 'system';

const THEMES: { value: Theme; label: string }[] = [
  { value: 'dark', label: 'ダーク' },
  { value: 'light', label: 'ライト' },
  { value: 'system', label: 'OSに合わせる' },
];

export function ViewSettingsMenu({ view, onChange, onReset, theme, onThemeChange }: {
  view: ViewSettings;
  onChange: (key: keyof ViewSettings, value: number) => void;
  onReset: () => void;
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  return (
    <div className="view-menu" ref={ref}>
      <button className="rescan" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        表示設定
      </button>
      {open && (
        <div className="view-menu-popover">
          <div className="view-menu-row">
            <span className="view-menu-label">テーマ</span>
            <div className="view-menu-theme">
              {THEMES.map((t) => (
                <button key={t.value} className={theme === t.value ? 'on' : ''} onClick={() => onThemeChange(t.value)}>
                  {t.label}
                </button>
              ))}
            </div>
            <span />
          </div>
          {ROWS.map(({ key, label, unit }) => (
            <label key={key} className="view-menu-row">
              <span className="view-menu-label">{label}</span>
              <input
                type="range"
                min={VIEW_LIMITS[key].min}
                max={VIEW_LIMITS[key].max}
                step={VIEW_LIMITS[key].step}
                value={view[key]}
                onChange={(e) => onChange(key, Number(e.target.value))}
              />
              <span className="view-menu-value">
                {view[key]}
                {unit}
              </span>
            </label>
          ))}
          <button className="view-menu-reset" onClick={onReset}>
            既定に戻す
          </button>
        </div>
      )}
    </div>
  );
}
