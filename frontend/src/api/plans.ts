import type { PlanDocument, PlanListResponse } from '@/types/shared'

interface PlanResponse {
  id: string
  session_id: string
  version: number
  document: PlanDocument
}

export async function listPlans(sessionId: string): Promise<PlanListResponse> {
  const res = await fetch(`/api/sessions/${sessionId}/plans`)
  if (!res.ok) throw new Error(`plan_list_failed_${res.status}`)
  return (await res.json()) as PlanListResponse
}

export async function getPlan(
  sessionId: string,
  version: number,
): Promise<PlanDocument> {
  const res = await fetch(`/api/sessions/${sessionId}/plans/${version}`)
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail ?? `plan_get_failed_${res.status}`)
  }
  const body = (await res.json()) as PlanResponse
  return body.document
}
