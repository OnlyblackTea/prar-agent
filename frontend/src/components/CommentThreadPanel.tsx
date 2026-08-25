import { useState } from 'react'
import type { CommentResponse } from '@/types/shared'

export interface SelectionSnapshot {
  from: number
  to: number
  quote: string
  quoteContext: string
}

interface CommentThreadPanelProps {
  comments: CommentResponse[]
  pendingSelection: SelectionSnapshot | null
  onCancel: () => void
  onSubmit: (body: string) => Promise<void>
  onJumpToAnchor: (anchorId: string) => void
  onApplyReviews?: () => void
  mergeBusy?: boolean
  unresolvedCount?: number
  /** 历史版本只读浏览：禁新评论输入、禁 Apply（设计 §3.4） */
  readonly?: boolean
  /** 回放失败（置信度 < 0.7）的评论，按 anchor_id 标识；悬空项显示提示并禁跳转（设计 §3.4） */
  danglingIds?: ReadonlySet<string>
}

export function CommentThreadPanel({
  comments,
  pendingSelection,
  onCancel,
  onSubmit,
  onJumpToAnchor,
  onApplyReviews,
  mergeBusy,
  unresolvedCount,
  readonly = false,
  danglingIds,
}: CommentThreadPanelProps) {
  const [body, setBody] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async () => {
    if (!body.trim()) return
    setSubmitting(true)
    try {
      await onSubmit(body.trim())
      setBody('')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <aside className="comment-panel" data-testid="comment-panel">
      <h3>Comments</h3>
      {!readonly && onApplyReviews && (
        <button
          className="apply-reviews-btn"
          onClick={onApplyReviews}
          disabled={mergeBusy || unresolvedCount === 0}
        >
          {mergeBusy ? 'Merging...' : `Apply Reviews (${unresolvedCount})`}
        </button>
      )}
      {!readonly && pendingSelection && (
        <div className="comment-new">
          <blockquote>{pendingSelection.quote}</blockquote>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Leave a comment..."
            maxLength={4000}
          />
          <div className="comment-new-actions">
            <button onClick={onCancel} disabled={submitting}>
              Cancel
            </button>
            <button
              onClick={handleSubmit}
              disabled={submitting || !body.trim()}
            >
              Submit
            </button>
          </div>
        </div>
      )}
      <ul className="comment-list">
        {comments.map((c) => {
          const dangling = danglingIds?.has(c.anchor_id) ?? false
          const className = [
            c.resolved ? 'comment-resolved' : null,
            dangling ? 'comment-dangling' : null,
          ]
            .filter(Boolean)
            .join(' ')
          return (
            <li
              key={c.id}
              className={className || undefined}
              onClick={() => {
                // 悬空评论无锚点可跳（设计 §3.4）
                if (!dangling) onJumpToAnchor(c.anchor_id)
              }}
            >
              <blockquote>{c.quote}</blockquote>
              {dangling && (
                <p className="comment-dangling-hint">⚠ 原文已变更，锚点无法定位</p>
              )}
              <p>{c.body}</p>
              <time>{new Date(c.created_at).toLocaleString()}</time>
            </li>
          )
        })}
      </ul>
    </aside>
  )
}
