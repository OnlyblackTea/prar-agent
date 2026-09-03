/** Session & Decision HTTP API 封装 */

export interface CreateSessionParams {
  init_request: string
  adapter_id: string
}

export interface SessionData {
  id: string
  init_request: string
  phase: string
  current_plan_version: number
  adapter_id: string
}

export async function createSession(params: CreateSessionParams): Promise<SessionData> {
  const res = await fetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    throw new Error(`createSession failed: ${res.status}`)
  }
  return res.json() as Promise<SessionData>
}

export async function answerDecision(
  sessionId: string,
  decisionId: string,
  answer: string,
): Promise<{ all_blocking_answered: boolean }> {
  const res = await fetch(`/api/sessions/${sessionId}/decisions/${decisionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answer }),
  })
  if (!res.ok) {
    throw new Error(`answerDecision failed: ${res.status}`)
  }
  return res.json()
}

export async function advanceToActing(sessionId: string): Promise<{ phase: string }> {
  const res = await fetch(`/api/sessions/${sessionId}/advance-to-acting`, {
    method: 'POST',
  })
  if (!res.ok) {
    throw new Error(`advanceToActing failed: ${res.status}`)
  }
  return res.json()
}

/** 登记局部 rerun；实际回退与重跑由 /act WS 消费 pending_rerun_from 执行（26 号契约） */
export async function requestRerun(
  sessionId: string,
  stepId: string,
): Promise<{ phase: string; rerun_from: string }> {
  const res = await fetch(`/api/sessions/${sessionId}/rerun`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ step_id: stepId }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail ?? `requestRerun failed: ${res.status}`)
  }
  return res.json()
}

export async function completeSession(sessionId: string): Promise<SessionData> {
  const res = await fetch(`/api/sessions/${sessionId}/complete`, {
    method: 'POST',
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail ?? `completeSession failed: ${res.status}`)
  }
  return res.json()
}
