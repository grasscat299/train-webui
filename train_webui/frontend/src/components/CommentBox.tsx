import { useEffect, useState } from 'react';
import { api } from '../api';

// run へのコメント（メモ）の編集欄。Train タブと Result タブの両方で使う。
// ツリーや run の定期更新で書きかけの内容が消えないよう、編集中（保存前）は外から来た値で上書きしない。
export function CommentBox({ runId, value, onSaved, placeholder }: {
  runId: string;
  value: string;
  onSaved: () => void;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState(value);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!dirty) setDraft(value);
  }, [value, dirty]);

  // 別の run に切り替わったら、その run の内容から始める
  useEffect(() => {
    setDraft(value);
    setDirty(false);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  const save = async () => {
    if (!dirty) return;
    setSaving(true);
    try {
      await api.updateComment(runId, draft);
      setDirty(false);
      setError(null);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="comment-box">
      <textarea
        className="comment-editor"
        value={draft}
        placeholder={placeholder ?? 'この run についてのメモ（何を試したか・結果の所感など）'}
        rows={Math.min(8, Math.max(2, draft.split('\n').length))}
        onChange={(e) => {
          setDraft(e.target.value);
          setDirty(true);
        }}
        onBlur={save}
        onKeyDown={(e) => {
          if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') save();
        }}
      />
      <div className="comment-status">
        {error ? <span className="log-error">保存に失敗しました: {error}</span> : saving ? '保存中…' : dirty ? '未保存（欄の外をクリックか Ctrl+Enter で保存）' : ''}
      </div>
    </div>
  );
}
