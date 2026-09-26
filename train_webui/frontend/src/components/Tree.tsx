import { useState } from 'react';
import type { GroupNode, ProjectNode, RunSummary } from '../api';
import { EyeToggle } from './EyeToggle';

export interface TreeProps {
  tree: ProjectNode[];
  mode: 'train' | 'result';
  busy: boolean;
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  onNewRunDraft: (project: string, group: string) => void;
  onNewGroup: (project: string) => void;
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

function ProjectRow({ node, mode, busy, selectedRunId, onSelectRun, onNewRunDraft, onNewGroup, onInfer,
  isRunChecked, isGroupChecked, isProjectChecked, toggleRun, toggleGroup, toggleProject }: { node: ProjectNode } & TreeProps) {
  const [open, setOpen] = useState(true);
  return (
    <div className="tree-project">
      <div className="tree-row tree-project-row">
        <button className="tree-toggle" onClick={() => setOpen((v) => !v)}>
          {open ? '▼' : '▶'}
        </button>
        {mode === 'result' && (
          <EyeToggle
            on={isProjectChecked(node.project, node.groups)}
            onChange={(on) => toggleProject(node.project, node.groups, on)}
          />
        )}
        <span className="tree-project-name">{node.project}</span>
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
              onInfer={onInfer}
              isRunChecked={isRunChecked}
              isGroupChecked={isGroupChecked}
              toggleRun={toggleRun}
              toggleGroup={toggleGroup}
            />
          ))}
          <button className="tree-add" onClick={() => onNewGroup(node.project)}>
            + 新規group
          </button>
        </div>
      )}
    </div>
  );
}

function GroupRow({ project, node, mode, busy, selectedRunId, onSelectRun, onNewRunDraft, onInfer,
  isRunChecked, isGroupChecked, toggleRun, toggleGroup }: {
  project: string;
  node: GroupNode;
  mode: 'train' | 'result';
  busy: boolean;
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  onNewRunDraft: (project: string, group: string) => void;
  onInfer: (project: string, group: string) => void;
  isRunChecked: (runId: string) => boolean;
  isGroupChecked: (project: string, group: string, runs: RunSummary[]) => boolean;
  toggleRun: (runId: string, val: boolean) => void;
  toggleGroup: (project: string, group: string, runs: RunSummary[], val: boolean) => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="tree-group">
      <div className="tree-row tree-group-row">
        <button className="tree-toggle" onClick={() => setOpen((v) => !v)}>
          {open ? '▼' : '▶'}
        </button>
        {mode === 'result' && (
          <EyeToggle
            on={isGroupChecked(project, node.group, node.runs)}
            onChange={(on) => toggleGroup(project, node.group, node.runs, on)}
          />
        )}
        <span className="tree-group-name">{node.group}</span>
        {mode === 'train' && (
          <button className="infer-btn" disabled={busy} onClick={() => onInfer(project, node.group)} title="model_{group}.pt で推論を実行">
            [infer]
          </button>
        )}
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
            </div>
          ))}
          {mode === 'train' && (
            <button className="tree-add" onClick={() => onNewRunDraft(project, node.group)}>
              + 新規run
            </button>
          )}
        </div>
      )}
    </div>
  );
}
