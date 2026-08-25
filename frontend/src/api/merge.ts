import type { MergerResult, PlanDocument } from '@/types/shared'

export interface MergeResponse {
  plan_version: number
  plan: PlanDocument
  merger_result: MergerResult
  plan_changed: boolean
}

export async function mergeReviews(sessionId: string): Promise<MergeResponse> {
  const res = await fetch(`/api/sessions/${sessionId}/merge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail ?? `merge_failed_${res.status}`)
  }
  return (await res.json()) as MergeResponse
}
