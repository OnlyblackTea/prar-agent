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
}

export function CommentThreadPanel({
  comments,
  pendingSelection,
  onCancel,
  onSubmit,
  onJumpToAnchor,
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
      {pendingSelection && (
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
          <li key={c.id} onClick={() => onJumpToAnchor(c.anchor_id)}>
            <blockquote>{c.quote}</blockquote>
            <p>{c.body}</p>
            <time>{new Date(c.created_at).toLocaleString()}</time>
          </li>
        ))}
      </ul>
    </aside>
  )
}
