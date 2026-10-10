import type { Comparison } from '../api';

// Result タブの上部: 名前を付けて残した比較を選ぶ・保存する・上書きする・名前を変える・消す。
// 選ぶと、その比較の run と表示ラベルがそのまま復元される（復元の処理は App 側）。
export function ComparisonBar({ comparisons, activeId, dirty, onSelect, onSaveNew, onOverwrite, onRename, onDelete }: {
  comparisons: Comparison[];
  activeId: string | null;
  // 選んでいる比較から、今の run・ラベルが変わっているか
  dirty: boolean;
  onSelect: (id: string | null) => void;
  onSaveNew: () => void;
  onOverwrite: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  const active = comparisons.find((c) => c.id === activeId) ?? null;
  return (
    <div className="comparison-bar">
      <span className="comparison-title">比較</span>
      <select
        className="comparison-select"
        value={active?.id ?? ''}
        onChange={(e) => onSelect(e.target.value || null)}
        title="保存した比較を選ぶと、その run と表示ラベルが復元される"
      >
        <option value="">（保存した比較を選ぶ）</option>
        {comparisons.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}（{c.run_ids.length}run）
          </option>
        ))}
      </select>
      {active && dirty && <span className="comparison-dirty" title="選んだ比較から run かラベルが変わっています">変更あり</span>}
      <button className="comparison-btn" onClick={onSaveNew} title="今の run と表示ラベルを新しい比較として保存する">
        新規保存
      </button>
      {active && (
        <>
          <button className="comparison-btn" onClick={onOverwrite} disabled={!dirty} title="選んだ比較を、今の run と表示ラベルで上書きする">
            上書き
          </button>
          <button className="comparison-btn" onClick={onRename}>名前変更</button>
          <button className="comparison-btn danger" onClick={onDelete}>削除</button>
        </>
      )}
    </div>
  );
}
