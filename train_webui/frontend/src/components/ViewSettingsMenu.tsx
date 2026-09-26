import { useEffect, useRef, useState } from 'react';
import type { ViewSettings } from '../viewSettings';
import { VIEW_LIMITS } from '../viewSettings';

const ROWS: { key: keyof ViewSettings; label: string; unit: string }[] = [
  { key: 'navWidth', label: 'ナビの幅', unit: 'px' },
  { key: 'panelWidth', label: 'グラフの幅', unit: 'px' },
  { key: 'chartHeight', label: 'グラフの高さ', unit: 'px' },
  { key: 'imageWidth', label: '画像の幅（一括）', unit: 'px' },
];

// 上部バーの「表示設定」: スライダーで大きさを変える。変更はすぐ全パネル・全画像に反映される。
export function ViewSettingsMenu({ view, onChange, onReset }: {
  view: ViewSettings;
  onChange: (key: keyof ViewSettings, value: number) => void;
  onReset: () => void;
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
