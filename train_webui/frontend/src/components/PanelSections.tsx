import { useState } from 'react';
import type { ReactNode } from 'react';

export interface PanelItem {
  label: string;
  subtitle?: string | null; // 軸つきグラフの title など、ラベルと別に出したい見出し
  wide?: boolean; // グリッドの全幅を使う（複数runの画像を並べる時など）
  content: ReactNode;
}

// ラベルの接頭辞（最初の "/" より前）。"/" が無いラベルは「その他」にまとめる。
function sectionOf(label: string): string {
  const i = label.indexOf('/');
  return i > 0 ? label.slice(0, i) : 'その他';
}

// wandb 風の表示: ラベルの接頭辞ごとに折りたたみ可能なセクションを作り、その中にパネルをグリッドで並べる。
export function PanelSections({ items }: { items: PanelItem[] }) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

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
                  <div key={item.label} className={`panel${item.wide ? ' panel-wide' : ''}`}>
                    <div className="panel-title">
                      <span>{item.label}</span>
                      {item.subtitle && <span className="panel-subtitle">{item.subtitle}</span>}
                    </div>
                    {item.content}
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
