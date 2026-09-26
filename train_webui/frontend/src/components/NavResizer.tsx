// ナビとメインの境目。ドラッグでナビの幅を変える（ダブルクリックで既定幅に戻す）。
// scale: 画面の大きさ（zoom）の倍率。マウスの移動量は画面上の px なので、倍率で割って拡大前の px に直す
// direction: 1 なら右へドラッグで広がる（左のナビ）、-1 なら左へドラッグで広がる（右のコメント欄）
export function NavResizer({ width, scale, onChange, onReset, direction = 1, title }: {
  width: number;
  scale: number;
  direction?: 1 | -1;
  title?: string;
  onChange: (w: number) => void;
  onReset: () => void;
}) {
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = width;
    const move = (ev: PointerEvent) => onChange(startWidth + (direction * (ev.clientX - startX)) / scale);
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      document.body.classList.remove('resizing');
    };
    document.body.classList.add('resizing');
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
  return (
    <div
      className="nav-resizer"
      role="separator"
      aria-orientation="vertical"
      title={title ?? "ドラッグでナビの幅を変更（ダブルクリックで元に戻す）"}
      onPointerDown={onPointerDown}
      onDoubleClick={onReset}
    />
  );
}
