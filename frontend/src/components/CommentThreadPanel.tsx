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
        {comments.map((c) => (
          <li
            key={c.id}
            className={c.resolved ? 'comment-resolved' : undefined}
            onClick={() => onJumpToAnchor(c.anchor_id)}
          >
            <blockquote>{c.quote}</blockquote>
            <p>{c.body}</p>
            <time>{new Date(c.created_at).toLocaleString()}</time>
          </li>
        ))}
      </ul>
    </aside>
  )
}
