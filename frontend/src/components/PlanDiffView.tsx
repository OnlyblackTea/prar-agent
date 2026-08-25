import type { PlanNode } from '@/types/shared'
import type { DiffRow } from '@/editor/diff'

/** 精简文本摘要渲染（不复用 Tiptap，保持抽屉轻量；设计 §3.4/§7） */
function nodeSummary(node: PlanNode): string {
  switch (node.type) {
    case 'heading':
      return `${'#'.repeat(node.level)} ${node.text}`
    case 'paragraph':
      return node.text
    case 'decision':
      return `[决策] ${node.question}`
    case 'glossary':
      return `[术语] ${node.term}：${node.definition}`
    case 'step':
      return `[步骤] ${node.title} — ${node.description}`
  }
}

const KIND_LABEL: Record<Exclude<DiffRow['kind'], 'unchanged'>, string> = {
  added: '新增',
  removed: '删除',
  modified: '修改',
}

export function PlanDiffView({ rows }: { rows: DiffRow[] }) {
  if (rows.length === 0) {
    return (
      <p className="diff-empty" data-testid="diff-empty">
        No changes
      </p>
    )
  }
  return (
    <ul className="plan-diff" data-testid="plan-diff">
      {rows.map((row, i) => {
        if (row.kind === 'modified' && row.oldNode && row.newNode) {
          return (
            <li key={i} className="diff-row diff-modified">
              <span className="diff-badge">修改</span>
              <del>{nodeSummary(row.oldNode)}</del>
              <ins>{nodeSummary(row.newNode)}</ins>
            </li>
          )
        }
        const node = row.kind === 'removed' ? row.oldNode : row.newNode
        if (!node) return null
        return (
          <li key={i} className={`diff-row diff-${row.kind}`}>
            {row.kind !== 'unchanged' && (
              <span className="diff-badge">
                {KIND_LABEL[row.kind as keyof typeof KIND_LABEL]}
              </span>
            )}
            <span className="diff-text">{nodeSummary(node)}</span>
          </li>
        )
      })}
    </ul>
  )
}
