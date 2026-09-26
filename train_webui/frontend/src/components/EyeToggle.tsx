// wandb の run 一覧と同じ「目」のアイコンで、比較対象に含めるか（表示するか）を切り替える。
export function EyeToggle({ on, onChange, title }: { on: boolean; onChange: (on: boolean) => void; title?: string }) {
  return (
    <button
      className={`eye-toggle${on ? ' on' : ''}`}
      title={title ?? (on ? '非表示にする' : '表示する')}
      aria-pressed={on}
      onClick={(e) => {
        e.stopPropagation();
        onChange(!on);
      }}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
        <circle cx="12" cy="12" r="3" />
        {!on && <line x1="3" y1="3" x2="21" y2="21" />}
      </svg>
    </button>
  );
}
