import { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import type { GroupNode, ProjectNode, RunSummary, StateResp } from './api';
import { usePolling } from './hooks/usePolling';
import { usePersistentState } from './hooks/usePersistentState';
import { Tree } from './components/Tree';
import { TrainPanel } from './components/TrainPanel';
import { ResultPanel } from './components/ResultPanel';
import { NavResizer } from './components/NavResizer';
import { CommentSidebar } from './components/CommentSidebar';
import type { SidebarRun } from './components/CommentSidebar';
import { ViewSettingsMenu } from './components/ViewSettingsMenu';
import type { Theme } from './components/ViewSettingsMenu';
import { DEFAULT_VIEW, ViewContext, useViewSettings, viewStyle } from './viewSettings';

type Tab = 'train' | 'result';

// コメント欄をこの幅（px）より狭くドラッグしたら閉じる
const COMMENT_CLOSE_WIDTH = 140;

export default function App() {
  const { view, update: updateView, reset: resetView } = useViewSettings();
  // 開いていたタブ・選んでいた run・Result の比較対象は、再読み込みしてもそのまま残す
  const [tab, setTab] = usePersistentState<Tab>('tab', 'train');
  const [selectedRunId, setSelectedRunId] = usePersistentState<string | null>('selectedRunId', null);
  const [runChecked, setRunChecked] = usePersistentState<Record<string, boolean>>('runChecked', {});
  const [groupChecked, setGroupChecked] = usePersistentState<Record<string, boolean>>('groupChecked', {});
  const [projectChecked, setProjectChecked] = usePersistentState<Record<string, boolean>>('projectChecked', {});
  const [actionError, setActionError] = useState<string | null>(null);
  // テーマ（既定はダーク）。index.html でも同じ保存値を読んで、描画前に data-theme を付けている
  const [theme, setTheme] = usePersistentState<Theme>('theme', 'dark');
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const { data: tree, setData: setTree } = usePolling<ProjectNode[]>(() => api.getTree(), 2500, []);
  const { data: state } = usePolling<StateResp>(() => api.getState(), 2500, []);
  const busy = state?.busy ?? false;
  const treeData = tree ?? [];

  // 保存していた run が削除されていたら選択を外す（ツリーを取得できてから判定する）
  useEffect(() => {
    if (tree === null || tree === undefined || selectedRunId === null) return;
    const exists = tree.some((p) => p.groups.some((g) => g.runs.some((r) => r.run_id === selectedRunId)));
    if (!exists) setSelectedRunId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tree]);

  const rescan = async () => {
    const t = await api.rescan();
    setTree(t);
  };

  const groupKey = (p: string, g: string) => `${p}::${g}`;
  const isRunChecked = (runId: string) => runChecked[runId] ?? false;
  const isGroupChecked = (p: string, g: string, runs: RunSummary[]) =>
    runs.length > 0 ? runs.every((r) => isRunChecked(r.run_id)) : (groupChecked[groupKey(p, g)] ?? false);
  const isProjectChecked = (p: string, groups: GroupNode[]) => {
    const allRuns = groups.flatMap((g) => g.runs);
    if (allRuns.length > 0) return groups.every((g) => isGroupChecked(p, g.group, g.runs));
    return projectChecked[p] ?? false;
  };

  const toggleRun = (runId: string, val: boolean) => setRunChecked((prev) => ({ ...prev, [runId]: val }));
  const toggleGroup = (p: string, g: string, runs: RunSummary[], val: boolean) => {
    setGroupChecked((prev) => ({ ...prev, [groupKey(p, g)]: val }));
    setRunChecked((prev) => {
      const next = { ...prev };
      runs.forEach((r) => {
        next[r.run_id] = val;
      });
      return next;
    });
  };
  const toggleProject = (p: string, groups: GroupNode[], val: boolean) => {
    setProjectChecked((prev) => ({ ...prev, [p]: val }));
    groups.forEach((g) => toggleGroup(p, g.group, g.runs, val));
  };

  const selectedRunIds = useMemo(() => {
    const ids: string[] = [];
    treeData.forEach((p) => p.groups.forEach((g) => g.runs.forEach((r) => {
      if (isRunChecked(r.run_id)) ids.push(r.run_id);
    })));
    return ids;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [treeData, runChecked]);

  // 右のコメント欄: Train タブでは選択中の run、Result タブでは比較中の run
  const [commentsOpen, setCommentsOpen] = usePersistentState<boolean>('commentsOpen', true);
  const sidebarRuns = useMemo<SidebarRun[]>(() => {
    const byId = new Map<string, SidebarRun>();
    treeData.forEach((p) => p.groups.forEach((g) => g.runs.forEach((r) => {
      byId.set(r.run_id, { runId: r.run_id, label: `${r.display_name} (${g.group_name})`, comment: r.comment });
    })));
    const ids = tab === 'train' ? (selectedRunId ? [selectedRunId] : []) : selectedRunIds;
    return ids.flatMap((id) => (byId.has(id) ? [byId.get(id)!] : []));
  }, [treeData, tab, selectedRunId, selectedRunIds]);

  const handleNewRunDraft = async (project: string, group: string) => {
    try {
      const run = await api.createDraft(project, group, false);
      await rescan();
      setSelectedRunId(run.run_id);
      setTab('train');
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  };

  // group_id / group_name は backend が自動で振る（名前は後からツリーの ✎ で変えられる）
  const handleNewGroup = async (project: string) => {
    try {
      const run = await api.createDraft(project, null, true);
      await rescan();
      setSelectedRunId(run.run_id);
      setTab('train');
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleRenameGroup = async (project: string, group: string, current: string) => {
    const name = window.prompt('group名を入力してください（checkpoint名などに使う group_id は変わりません）', current);
    if (name === null || !name.trim() || name.trim() === current) return;
    try {
      await api.renameGroup(project, group, name.trim());
      await rescan();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleInfer = async (project: string, group: string) => {
    try {
      await api.groupInfer(project, group);
      await rescan();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <ViewContext.Provider value={{ view, update: updateView }}>
      <div className="app" style={viewStyle(view)}>
        <header className="topbar">
          <h1>学習管理WebUI</h1>
          <nav className="tabs">
            <button className={tab === 'train' ? 'active' : ''} onClick={() => setTab('train')}>
              Train
            </button>
            <button className={tab === 'result' ? 'active' : ''} onClick={() => setTab('result')}>
              Result
            </button>
          </nav>
          <ViewSettingsMenu view={view} onChange={updateView} onReset={resetView} theme={theme} onThemeChange={setTheme} />
          <button className="rescan" onClick={rescan}>
            再スキャン
          </button>
          {busy && (
            <span className="busy-badge">
              GPU使用中{state?.active ? `: ${state.active.run_id} (${state.active.kind})` : ''}
            </span>
          )}
        </header>
        {actionError && (
          <div className="error-banner clickable" onClick={() => setActionError(null)}>
            {actionError}（クリックで閉じる）
          </div>
        )}
        <div className="layout">
          <Tree
            tree={treeData}
            mode={tab}
            busy={busy}
            selectedRunId={selectedRunId}
            onSelectRun={setSelectedRunId}
            onNewRunDraft={handleNewRunDraft}
            onNewGroup={handleNewGroup}
            onRenameGroup={handleRenameGroup}
            onInfer={handleInfer}
            isRunChecked={isRunChecked}
            isGroupChecked={isGroupChecked}
            isProjectChecked={isProjectChecked}
            toggleRun={toggleRun}
            toggleGroup={toggleGroup}
            toggleProject={toggleProject}
          />
          <NavResizer
            width={view.navWidth}
          scale={view.uiScale / 100}
            onChange={(w) => updateView('navWidth', w)}
            onReset={() => updateView('navWidth', DEFAULT_VIEW.navWidth)}
          />
          <main className="main">
            {tab === 'train' ? (
              <TrainPanel
                runId={selectedRunId}
                busy={busy}
                onRunChanged={rescan}
                onDeleted={() => {
                  setSelectedRunId(null);
                  rescan();
                }}
              />
            ) : (
              <ResultPanel runIds={selectedRunIds} tree={treeData} />
            )}
          </main>
          {/* 閉じている時も出しておき、左へドラッグすれば開き直せる（幅0から広げる扱い） */}
          <NavResizer
            width={commentsOpen ? view.commentWidth : 0}
            scale={view.uiScale / 100}
            direction={-1}
            title="ドラッグでコメント欄の幅を変更。細くすると閉じ、閉じている時は左へドラッグで開く（ダブルクリックで元に戻す）"
            onChange={(w) => {
              // 一定の細さより狭くドラッグしたら閉じる。同じドラッグのまま広げ直せば開き直す
              if (w < COMMENT_CLOSE_WIDTH) {
                setCommentsOpen(false);
              } else {
                setCommentsOpen(true);
                updateView('commentWidth', w);
              }
            }}
            onReset={() => {
              setCommentsOpen(true);
              updateView('commentWidth', DEFAULT_VIEW.commentWidth);
            }}
          />
          <CommentSidebar
            open={commentsOpen}
            onToggle={() => setCommentsOpen(!commentsOpen)}
            runs={sidebarRuns}
            emptyText={tab === 'train' ? '左のツリーで run を選ぶと、その run のコメントを書けます。' : '比較する run を目のアイコンで選ぶと、ここに並びます。'}
            onSaved={rescan}
          />
        </div>
      </div>
    </ViewContext.Provider>
  );
}
