import { useEffect, useState } from 'react';
import { api } from '../api';

// group（=モデル構成）の説明（Markdown）の編集欄。Claude が「このモデルはどう作られているか」を知るための文書で、
// `cli show` / `view` / `compare` の出力に載る。同じ group の run すべてで共通。
// 編集中（保存前）は、定期更新で来た値で上書きしない（CommentBox と同じ）。
export function GroupDescription({ project, group, groupName, value, onSaved }: {
  project: string;
  group: string;
  groupName: string;
  value: string;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState(value);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!dirty) setDraft(value);
  }, [value, dirty]);

  // 別の group に切り替わったら、その group の内容から始める
  useEffect(() => {
    setDraft(value);
    setDirty(false);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, group]);

  const save = async () => {
    if (!dirty) return;
    setSaving(true);
    try {
      await api.updateGroupDescription(project, group, draft);
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
    <details className="group-description" open={value.trim() === ''}>
      <summary>
        モデルの説明（{groupName}）{value.trim() === '' && <span className="group-description-empty">未記入</span>}
      </summary>
      <div className="group-description-hint">
        この group のモデルがどう作られているか（構成・入出力・損失・他の group との違い）を Markdown で書く。
        同じ group の run すべてで共通で、Claude が結果を読む時の前提知識になる。
      </div>
      <textarea
        className="comment-editor group-description-editor"
        value={draft}
        rows={Math.min(30, Math.max(10, draft.split('\n').length + 1))}
        placeholder={'# モデル概要\n\n## 構成\n\n## 入力と出力\n\n## 損失\n\n## 他の group との違い'}
        onChange={(e) => {
          setDraft(e.target.value);
          setDirty(true);
        }}
        onBlur={save}
        onKeyDown={(e) => {
          if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') save();
        }}
      />
      {(error || saving || dirty) && (
        <div className="comment-status">
          {error ? <span className="log-error">保存に失敗しました: {error}</span> : saving ? '保存中…' : '未保存（欄の外をクリックか Ctrl+Enter で保存）'}
        </div>
      )}
    </details>
  );
}
