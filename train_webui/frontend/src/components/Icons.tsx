import type { ReactNode } from 'react';

// ツリーの操作ボタン用の線画アイコン（EyeToggle と同じ 24x24・線幅2 のスタイル）
function Svg({ children }: { children: ReactNode }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  );
}

export const PlusIcon = () => (
  <Svg>
    <line x1="12" y1="5" x2="12" y2="19" />
    <line x1="5" y1="12" x2="19" y2="12" />
  </Svg>
);

export const PlayIcon = () => (
  <Svg>
    <polygon points="7 4 20 12 7 20 7 4" />
  </Svg>
);

export const PencilIcon = () => (
  <Svg>
    <path d="M17 3a2.85 2.85 0 0 1 4 4L7.5 20.5 2 22l1.5-5.5z" />
  </Svg>
);

export const CopyIcon = () => (
  <Svg>
    <rect x="9" y="9" width="13" height="13" rx="2" />
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </Svg>
);

export const CheckIcon = () => (
  <Svg>
    <polyline points="20 6 9 17 4 12" />
  </Svg>
);

export const ChevronIcon = ({ open }: { open: boolean }) => (
  <Svg>
    <polyline points={open ? '6 9 12 15 18 9' : '9 6 15 12 9 18'} />
  </Svg>
);

// 行にマウスを乗せた時・フォーカスした時だけ見えるアイコンボタン（表示・非表示は CSS の .row-actions で制御）
export function IconButton({ title, onClick, disabled, children }: {
  title: string;
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <button className="icon-btn" title={title} aria-label={title} disabled={disabled} onClick={(e) => { e.stopPropagation(); onClick(); }}>
      {children}
    </button>
  );
}
