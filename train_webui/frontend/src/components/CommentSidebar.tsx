import { CommentBox } from './CommentBox';

export interface SidebarRun {
  runId: string;
  label: string; // "v2 (group名)"
  comment: string;
}

// 右側の開閉できるコメント欄。Train タブでは選択中の run、Result タブでは比較中の run のコメントを並べる。
export function CommentSidebar({ open, onToggle, runs, emptyText, onSaved }: {
  open: boolean;
  onToggle: () => void;
  runs: SidebarRun[];
  emptyText: string;
  onSaved: () => void;
}) {
  if (!open) {
    return (
      <aside className="comment-sidebar closed">
        <button className="comment-sidebar-toggle" onClick={onToggle} title="コメント欄を開く" aria-label="コメント欄を開く">
          <CommentIcon />
          {runs.length > 0 && <span className="comment-sidebar-count">{runs.length}</span>}
        </button>
      </aside>
    );
  }
  return (
    <aside className="comment-sidebar">
      <div className="comment-sidebar-header">
        <CommentIcon />
        <span>コメント</span>
        <button className="comment-sidebar-toggle" onClick={onToggle} title="コメント欄を閉じる" aria-label="コメント欄を閉じる">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <polyline points="9 6 15 12 9 18" />
          </svg>
        </button>
      </div>
      <div className="comment-sidebar-body">
        {runs.length === 0 && <div className="note">{emptyText}</div>}
        {runs.map((r) => (
          <div key={r.runId} className="comment-item">
            <div className="comment-item-title" title={r.runId}>
              {r.label}
            </div>
            <CommentBox runId={r.runId} value={r.comment} onSaved={onSaved} />
          </div>
        ))}
      </div>
    </aside>
  );
}

function CommentIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}
