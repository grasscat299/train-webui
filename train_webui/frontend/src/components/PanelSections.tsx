import { usePersistentState } from '../hooks/usePersistentState';
import type { PointerEvent as ReactPointerEvent, ReactNode } from 'react';
import { useView } from '../viewSettings';
import { tipProps } from '../hoverTip';

export interface PanelItem {
  label: string;
  subtitle?: string | null; // 軸つきグラフの title など、ラベルと別に出したい見出し
  tip?: string; // マウスを乗せたときに出す説明（指標の意味など）
  wide?: boolean; // グリッドの全幅を使う（複数runの画像を並べる時など）
  // 右下のつまみをドラッグした時に変える大きさ。chart: 全グラフの幅と高さ、image: 全画像の幅（比較しやすいよう一括で変える）
  resize?: 'chart' | 'image';
  content: ReactNode;
}

// ラベルの接頭辞（最初の "/" より前）。"/" が無いラベルは「その他」にまとめる。
function sectionOf(label: string): string {
  const i = label.indexOf('/');
  return i > 0 ? label.slice(0, i) : 'その他';
}

// wandb 風の表示: ラベルの接頭辞ごとに折りたたみ可能なセクションを作り、その中にパネルをグリッドで並べる。
export function PanelSections({ items }: { items: PanelItem[] }) {
  // セクションの開閉は再読み込みしても残す
  const [collapsed, setCollapsed] = usePersistentState<Record<string, boolean>>('collapsedSections', {});
  const { view, update } = useView();

  // corner: 右下の角（幅と高さ）、right: 右辺（幅だけ）
  const startResize = (e: ReactPointerEvent<HTMLDivElement>, mode: 'chart' | 'image', handle: 'corner' | 'right') => {
    e.preventDefault();
    const panel = (e.currentTarget.parentElement as HTMLElement).getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const start = { ...view };
    // 画面の大きさ（zoom）で拡大しているので、画面上の px を拡大前の px に直す
    const scale = view.uiScale / 100;
    const startPanelWidth = panel.width / scale;
    const move = (ev: PointerEvent) => {
      const dx = (ev.clientX - startX) / scale;
      const dy = (ev.clientY - startY) / scale;
      if (mode === 'chart') {
        // グリッドの 1fr で最小幅より広がっていることがあるので、実際の幅を起点にする
        update('panelWidth', startPanelWidth + dx);
        if (handle === 'corner') update('chartHeight', start.chartHeight + dy);
      } else {
        update('imageWidth', start.imageWidth + dx);
      }
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      document.body.classList.remove('resizing-panel', 'resizing-panel-x');
    };
    document.body.classList.add(handle === 'corner' ? 'resizing-panel' : 'resizing-panel-x');
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  const sections = new Map<string, PanelItem[]>();
  items
    .slice()
    .sort((a, b) => a.label.localeCompare(b.label))
    .forEach((item) => {
      const name = sectionOf(item.label);
      sections.set(name, [...(sections.get(name) ?? []), item]);
    });
  const names = Array.from(sections.keys()).sort((a, b) =>
    a === 'その他' ? 1 : b === 'その他' ? -1 : a.localeCompare(b),
  );

  if (items.length === 0) return <div className="panel-empty">まだ結果がありません。</div>;

  return (
    <div className="sections">
      {names.map((name) => {
        const list = sections.get(name) ?? [];
        const isCollapsed = collapsed[name] ?? false;
        return (
          <section key={name} className="section">
            <button
              className="section-header"
              onClick={() => setCollapsed((prev) => ({ ...prev, [name]: !isCollapsed }))}
            >
              <span className="section-caret">{isCollapsed ? '▸' : '▾'}</span>
              <span className="section-name">{name}</span>
              <span className="section-count">{list.length}</span>
            </button>
            {!isCollapsed && (
              <div className="panel-grid">
                {list.map((item) => (
                  <div
                    key={item.label}
                    className={`panel${item.wide ? ' panel-wide' : ''}${item.resize === 'image' ? ' panel-image' : ''}`}
                  >
                    <div className="panel-title" {...tipProps(item.tip)}>
                      <span>{item.label}</span>
                      {item.subtitle && <span className="panel-subtitle">{item.subtitle}</span>}
                    </div>
                    {item.content}
                    <div
                      className="panel-resize-right"
                      title={(item.resize ?? 'chart') === 'chart' ? 'ドラッグで全グラフの幅を変更' : 'ドラッグで全画像の幅を変更'}
                      onPointerDown={(e) => startResize(e, item.resize ?? 'chart', 'right')}
                    />
                    <div
                      className="panel-resize"
                      title={
                        (item.resize ?? 'chart') === 'chart'
                          ? 'ドラッグで全グラフの大きさを変更'
                          : 'ドラッグで全画像の幅を変更'
                      }
                      onPointerDown={(e) => startResize(e, item.resize ?? 'chart', 'corner')}
                    />
                  </div>
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
