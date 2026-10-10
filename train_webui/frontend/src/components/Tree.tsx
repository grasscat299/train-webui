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
      {props.tree.map((p) => (
        <ProjectRow key={p.project} node={p} {...props} />
      ))}
    </aside>
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
  const [open, setOpen] = usePersistentState(`treeOpen.${project}\u0000${node.group}`, true);
  // 直近にコピーした run（アイコンをチェックにして知らせる）
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const copyRunId = async (runId: string) => {
    if (!(await copyText(runId))) return;
    setCopiedId(runId);
    window.setTimeout(() => setCopiedId((cur) => (cur === runId ? null : cur)), 1200);
  };
  return (
    <div className="tree-group">
      <div className="tree-row tree-group-row">
        <button className="tree-toggle" onClick={() => setOpen((v) => !v)} aria-label={open ? '閉じる' : '開く'}>
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
          {node.runs.map((r) => (
            <div key={r.run_id} className={`tree-row tree-run-row ${selectedRunId === r.run_id ? 'selected' : ''}`}>
              {mode === 'result' && (
                <EyeToggle on={isRunChecked(r.run_id)} onChange={(on) => toggleRun(r.run_id, on)} />
              )}
              <button className="tree-run-btn" onClick={() => onSelectRun(r.run_id)}>
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
          ))}
        </div>
      )}
    </div>
  );
}
