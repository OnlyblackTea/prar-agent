import type { CommentResponse } from '@/types/shared'

export interface CreateCommentBody {
  anchor_id: string
  plan_version: number
  quote: string
  quote_context: string
  body: string
}

export async function createComment(
  sessionId: string,
  payload: CreateCommentBody,
): Promise<CommentResponse> {
  const res = await fetch(`/api/sessions/${sessionId}/comments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail ?? `comment_create_failed_${res.status}`)
  }
  return (await res.json()) as CommentResponse
}

export async function listComments(
  sessionId: string,
  planVersion: number,
): Promise<CommentResponse[]> {
  const res = await fetch(
    `/api/sessions/${sessionId}/comments?plan_version=${planVersion}`,
  )
  if (!res.ok) throw new Error(`comment_list_failed_${res.status}`)
  return (await res.json()) as CommentResponse[]
}
