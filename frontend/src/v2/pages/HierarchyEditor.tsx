import { useState } from 'react';
import { useHierarchyChange, type DepartmentOverview } from '../../api/queries';
import { he } from '../../i18n/he';
import { Drawer } from '../components/Drawer';

const s = he.v2.deptPage;

type Node = DepartmentOverview['tree'][number];
type Mode = 'below' | 'above' | 'move';

export function HierarchyEditor({
  departmentId,
  tree,
  editable,
}: {
  departmentId: number;
  tree: Node[];
  editable: boolean;
}) {
  const [target, setTarget] = useState<{ mode: Mode; node: Node | null } | null>(null);
  const [name, setName] = useState('');
  const [parent, setParent] = useState('');
  const [reason, setReason] = useState('');
  const [key, setKey] = useState(() => crypto.randomUUID());
  const change = useHierarchyChange();
  const render = (parentId: number | null) => {
    const kids = tree.filter((n) => n.parent_group_id === parentId);
    if (!kids.length) return null;
    return (
      <ul>
        {kids.map((n) => (
          <li key={n.id}>
            {n.name}
            {editable && (
              <>
                {(['below', 'above', 'move'] as Mode[]).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setTarget({ mode, node: n })}
                    aria-label={`${mode === 'below' ? s.addBelow : mode === 'above' ? s.addAbove : s.move}: ${n.name}`}
                  >
                    {mode === 'below' ? s.addBelow : mode === 'above' ? s.addAbove : s.move}
                  </button>
                ))}
              </>
            )}
            {render(n.id)}
          </li>
        ))}
      </ul>
    );
  };
  const close = () => {
    setTarget(null);
    setName('');
    setParent('');
    setReason('');
    setKey(crypto.randomUUID());
    change.reset();
  };
  const submit = () => {
    if (!target?.node) return;
    const n = target.node;
    const parentId = parent ? Number(parent) : null;
    const c =
      target.mode === 'below'
        ? { kind: 'below' as const, departmentId, parentId: n.id, name }
        : target.mode === 'above'
          ? { kind: 'above' as const, groupId: n.id, name }
          : { kind: 'move' as const, groupId: n.id, parentId };
    change.mutate({ change: c, reason, key });
  };
  return (
    <section className="v2-card">
      <h2>{s.hierarchy}</h2>
      {render(null)}
      {target?.node && (
        <Drawer opened onClose={close} title={`${s.hierarchy}: ${target.node.name}`}>
          {change.isSuccess ? (
            <p role="status">{s.saved}</p>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                submit();
              }}
            >
              {target.mode === 'move' ? (
                <label>
                  {s.newParent}
                  <select value={parent} onChange={(e) => setParent(e.target.value)}>
                    <option value="">{s.root}</option>
                    {tree
                      .filter((n) => n.id !== target.node!.id)
                      .map((n) => (
                        <option key={n.id} value={n.id}>
                          {n.name}
                        </option>
                      ))}
                  </select>
                </label>
              ) : (
                <label>
                  {s.newName}
                  <input required value={name} onChange={(e) => setName(e.target.value)} />
                </label>
              )}
              <label>
                {s.reason}
                <input required value={reason} onChange={(e) => setReason(e.target.value)} />
              </label>
              <button type="submit" disabled={change.isPending}>
                {s.save}
              </button>
              {change.isError && <p role="alert">{(change.error as Error).message}</p>}
            </form>
          )}
        </Drawer>
      )}
    </section>
  );
}
