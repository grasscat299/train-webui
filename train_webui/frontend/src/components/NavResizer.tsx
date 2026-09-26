// ナビとメインの境目。ドラッグでナビの幅を変える（ダブルクリックで既定幅に戻す）。
export function NavResizer({ width, onChange, onReset }: { width: number; onChange: (w: number) => void; onReset: () => void }) {
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = width;
    const move = (ev: PointerEvent) => onChange(startWidth + ev.clientX - startX);
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
      title="ドラッグでナビの幅を変更（ダブルクリックで元に戻す）"
      onPointerDown={onPointerDown}
      onDoubleClick={onReset}
    />
  );
}
