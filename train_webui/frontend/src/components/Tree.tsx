import type { GroupNode, ProjectNode, RunSummary } from '../api';
import { EyeToggle } from './EyeToggle';
import { usePersistentState } from '../hooks/usePersistentState';
import { useState } from 'react';
import { copyText } from '../copyText';
import { CheckIcon, ChevronIcon, CopyIcon, IconButton, PencilIcon, PlayIcon, PlusIcon } from './Icons';

export interface TreeProps {
  tree: ProjectNode[];
  mode: 'train' | 'result';
  busy: boolean;
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  onNewRunDraft: (project: string, group: string) => void;
  onNewGroup: (project: string) => void;
  onRenameGroup: (project: string, group: string, current: string) => void;
  onInfer: (project: string, group: string) => void;
  isRunChecked: (runId: string) => boolean;
  isGroupChecked: (project: string, group: string, runs: RunSummary[]) => boolean;
  isProjectChecked: (project: string, groups: GroupNode[]) => boolean;
  toggleRun: (runId: string, val: boolean) => void;
  toggleGroup: (project: string, group: string, runs: RunSummary[], val: boolean) => void;
  toggleProject: (project: string, groups: GroupNode[], val: boolean) => void;
}

export function Tree(props: TreeProps) {
  return (
    <aside className="tree">
      {props.tree.length === 0 && <div className="tree-empty">projectが見つかりません</div>}
      {groupByWorkspace(props.tree).map((ws) => (
        <HeaderRow key={ws.name} label={ws.name} storeKey={`treeOpen.ws.${ws.name}`} className="tree-workspace-row">
          {ws.stages.map((st) => (
            <HeaderRow key={st.name} label={st.name} storeKey={`treeOpen.stage.${ws.name}\u0000${st.name}`} className="tree-stage-row">
              {st.models.map((p) => (
                <ProjectRow key={p.project} node={p} {...props} />
              ))}
            </HeaderRow>
          ))}
        </HeaderRow>
      ))}
    </aside>
  );
}

// プロジェクト > 段階（本番/scratch）> ディレクトリ（ProjectNode）の順に、tree の出現順を保ってまとめる
function groupByWorkspace(tree: ProjectNode[]) {
  const result: { name: string; stages: { name: string; models: ProjectNode[] }[] }[] = [];
  for (const p of tree) {
    const wsName = p.workspace ?? '未分類';
    const stName = p.stage ?? '';
    let ws = result.find((w) => w.name === wsName);
    if (!ws) result.push((ws = { name: wsName, stages: [] }));
    let st = ws.stages.find((x) => x.name === stName);
    if (!st) ws.stages.push((st = { name: stName, models: [] }));
    st.models.push(p);
  }
  return result;
}

// group 内の run を、引き継ぎ学習の親子（parent_run_id）の木にする。親が group に無い run は根に置く
interface RunTree { run: RunSummary; children: RunTree[] }
function buildRunTree(runs: RunSummary[]): RunTree[] {
  const nodes = new Map<string, RunTree>(runs.map((r) => [r.run_id, { run: r, children: [] }]));
  const roots: RunTree[] = [];
  for (const r of runs) {
    const parent = r.parent_run_id && r.parent_run_id !== r.run_id ? nodes.get(r.parent_run_id) : undefined;
    (parent ? parent.children : roots).push(nodes.get(r.run_id)!);
  }
  return roots;
}

function HeaderRow({ label, storeKey, className, children }: {
  label: string; storeKey: string; className: string; children: React.ReactNode;
}) {
  const [open, setOpen] = usePersistentState(storeKey, true);
  return (
    <div>
      <div className={`tree-row ${className}`}>
        <button className="tree-toggle" onClick={() => setOpen((v) => !v)} aria-label={open ? '閉じる' : '開く'}>
          <ChevronIcon open={open} />
        </button>
        <span className="tree-project-name">{label}</span>
      </div>
      {open && <div className="tree-children">{children}</div>}
    </div>
  );
}

function ProjectRow({ node, mode, busy, selectedRunId, onSelectRun, onNewRunDraft, onNewGroup, onRenameGroup, onInfer,
  isRunChecked, isGroupChecked, isProjectChecked, toggleRun, toggleGroup, toggleProject }: { node: ProjectNode } & TreeProps) {
  const [open, setOpen] = usePersistentState(`treeOpen.${node.project}`, true);
  return (
    <div className="tree-project">
      <div className="tree-row tree-project-row">
        <button className="tree-toggle" onClick={() => setOpen((v) => !v)} aria-label={open ? '閉じる' : '開く'}>
          <ChevronIcon open={open} />
        </button>
        {mode === 'result' && (
          <EyeToggle
            on={isProjectChecked(node.project, node.groups)}
            onChange={(on) => toggleProject(node.project, node.groups, on)}
          />
        )}
        <span className="tree-project-name" title={node.project}>{node.project}</span>
        <span className="row-actions">
          <IconButton title="新規group" onClick={() => onNewGroup(node.project)}>
            <PlusIcon />
          </IconButton>
        </span>
      </div>
      {open && (
        <div className="tree-children">
          {node.groups.map((g) => (
            <GroupRow
              key={g.group}
              project={node.project}
              node={g}
              mode={mode}
              busy={busy}
              selectedRunId={selectedRunId}
              onSelectRun={onSelectRun}
              onNewRunDraft={onNewRunDraft}
              onRenameGroup={onRenameGroup}
              onInfer={onInfer}
              isRunChecked={isRunChecked}
              isGroupChecked={isGroupChecked}
              toggleRun={toggleRun}
              toggleGroup={toggleGroup}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function GroupRow({ project, node, mode, busy, selectedRunId, onSelectRun, onNewRunDraft, onRenameGroup, onInfer,
  isRunChecked, isGroupChecked, toggleRun, toggleGroup }: {
  project: string;
  node: GroupNode;
  mode: 'train' | 'result';
  busy: boolean;
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  onNewRunDraft: (project: string, group: string) => void;
  onRenameGroup: (project: string, group: string, current: string) => void;
  onInfer: (project: string, group: string) => void;
  isRunChecked: (runId: string) => boolean;
  isGroupChecked: (project: string, group: string, runs: RunSummary[]) => boolean;
  toggleRun: (runId: string, val: boolean) => void;
  toggleGroup: (project: string, group: string, runs: RunSummary[], val: boolean) => void;
}) {
  const [open, setOpen] = usePersistentState(`treeOpenRuns.${project}\u0000${node.group}`, false);
  // 直近にコピーした run（アイコンをチェックにして知らせる）
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const copyRunId = async (runId: string) => {
    if (!(await copyText(runId))) return;
    setCopiedId(runId);
    window.setTimeout(() => setCopiedId((cur) => (cur === runId ? null : cur)), 1200);
  };
  const renderRun = ({ run: r, children }: RunTree, depth: number): React.ReactNode => (
    <div key={r.run_id}>
      <div className={`tree-row tree-run-row ${selectedRunId === r.run_id ? 'selected' : ''}`} style={{ paddingLeft: depth * 14 }}>
        {mode === 'result' && (
          <EyeToggle on={isRunChecked(r.run_id)} onChange={(on) => toggleRun(r.run_id, on)} />
        )}
        <button className="tree-run-btn" onClick={() => onSelectRun(r.run_id)}>
          {depth > 0 && <span className="run-lineage" title="引き継ぎ学習">↳</span>}
          <span className={`status-dot status-${r.status}`} title={r.status} />
          <span className="run-name" title={r.run_id}>
            {r.display_name}
          </span>
          {r.origin === 'cli' && <span className="cli-badge">CLI</span>}
        </button>
        <span className={`row-actions${copiedId === r.run_id ? ' shown' : ''}`}>
          <IconButton title="run_id をコピー（Claude に貼ると、この run を読める）" onClick={() => copyRunId(r.run_id)}>
            {copiedId === r.run_id ? <CheckIcon /> : <CopyIcon />}
          </IconButton>
        </span>
      </div>
      {children.map((c) => renderRun(c, depth + 1))}
    </div>
  );
  return (
    <div className="tree-group">
      <div className="tree-row tree-group-row">
        <button className="tree-toggle" onClick={() => setOpen((v) => !v)} aria-label={open ? 'runを隠す' : 'runを表示'}>
          <ChevronIcon open={open} />
        </button>
        {mode === 'result' && (
          <EyeToggle
            on={isGroupChecked(project, node.group, node.runs)}
            onChange={(on) => toggleGroup(project, node.group, node.runs, on)}
          />
        )}
        <span className="tree-group-name" title={`${node.group_name}（group_id: ${node.group}）`}>
          {node.group_name}
        </span>
        <span className="row-actions">
          <IconButton title="新規run（このgroupに下書きを作る）" onClick={() => onNewRunDraft(project, node.group)}>
            <PlusIcon />
          </IconButton>
          {/* Train / Result どちらのタブでも group 単位で infer を起動できる（結果は Result タブに自動で反映される） */}
          <IconButton title="infer（model_{group_id}.pt で推論）" disabled={busy} onClick={() => onInfer(project, node.group)}>
            <PlayIcon />
          </IconButton>
          <IconButton title="group名を変更" onClick={() => onRenameGroup(project, node.group, node.group_name)}>
            <PencilIcon />
          </IconButton>
        </span>
      </div>
      {open && (
        <div className="tree-children">
          {buildRunTree(node.runs).map((t) => renderRun(t, 0))}
        </div>
      )}
    </div>
  );
}
