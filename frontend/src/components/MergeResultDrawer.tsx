import type { MergerResult, PlanDocument } from '@/types/shared'
import { diffPlans } from '@/editor/diff'
import { PlanDiffView } from './PlanDiffView'

export interface MergeResultDrawerProps {
  result: MergerResult
  planChanged: boolean
  newVersion: number
  prevPlan: PlanDocument | null
  newPlan: PlanDocument
  onClose: () => void
}

/**
 * merge 结果抽屉（替换 alert，决策 §13-1.A）：
 * 决策列表（徽标 + reason）+ 节点级三色 diff。
 * 全 reject 时抽屉照常打开，顶部提示 "Plan unchanged"。
 */
export function MergeResultDrawer({
  result,
  planChanged,
  newVersion,
  prevPlan,
  newPlan,
  onClose,
}: MergeResultDrawerProps) {
  const rows = diffPlans(prevPlan?.nodes ?? [], newPlan.nodes)
  const changedRows = rows.filter((r) => r.kind !== 'unchanged')

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <div
        className="merge-drawer"
        data-testid="merge-drawer"
        role="dialog"
        aria-label="Merge result"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="drawer-header">
          <h3>
            {planChanged ? `Plan v${newVersion}` : 'Plan unchanged'}
          </h3>
          <button
            className="drawer-close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {!planChanged && (
          <p className="drawer-banner" data-testid="drawer-unchanged-banner">
            Plan unchanged — all comments rejected
          </p>
        )}

        {result.overall_comment && (
          <p className="drawer-overall">{result.overall_comment}</p>
        )}

        <ul className="drawer-decisions">
          {result.actions.map((a) => (
            <li
              key={a.comment_id}
              className={`drawer-decision drawer-decision-${a.decision}`}
            >
              <span className="decision-tag">{a.decision}</span>
              <span className="decision-reason">{a.reason}</span>
            </li>
          ))}
        </ul>

        <h4>Changes</h4>
        <PlanDiffView rows={planChanged ? changedRows : []} />
      </div>
    </div>
  )
}
