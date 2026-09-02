import type { SessionAction } from '@/state/sessionReducer'

// ===== /act WS 事件类型（19 号契约，前端侧定义） =====

export interface ActStepStartEvent {
  type: 'step.start'
  index: number
  step_id: string
  title: string
  tool: string
  tool_args: Record<string, unknown>
}

export interface ActToolStdoutEvent {
  type: 'tool.stdout'
  step_id: string
  chunk: string
}

export interface ActToolExitEvent {
  type: 'tool.exit'
  step_id: string
  exit_code: number
  ok: boolean
}

export interface ActStepDoneEvent {
  type: 'step.done'
  step_id: string
  ok: boolean
  attempts: number
  output: string
  artifacts: string[]
  thoughts: string[]
  failure_reason: string | null
  git_commit: string | null
}

export interface ActPlanDoneEvent {
  type: 'plan.done'
  total_steps: number
  all_ok: boolean
}

export interface ActErrorEvent {
  type: 'error'
  code: string
  message: string
}

export type ActEvent =
  | ActStepStartEvent
  | ActToolStdoutEvent
  | ActToolExitEvent
  | ActStepDoneEvent
  | ActPlanDoneEvent
  | ActErrorEvent

export function actEventToAction(event: ActEvent): SessionAction | null {
  switch (event.type) {
    case 'step.start':
      return {
        type: 'WS_ACT_STEP_START',
        index: event.index,
        step_id: event.step_id,
        title: event.title,
        tool: event.tool,
        tool_args: event.tool_args,
      }
    case 'tool.stdout':
      return { type: 'WS_ACT_TOOL_STDOUT', step_id: event.step_id, chunk: event.chunk }
    case 'tool.exit':
      return {
        type: 'WS_ACT_TOOL_EXIT',
        step_id: event.step_id,
        exit_code: event.exit_code,
        ok: event.ok,
      }
    case 'step.done':
      return {
        type: 'WS_ACT_STEP_DONE',
        step_id: event.step_id,
        ok: event.ok,
        attempts: event.attempts,
        output: event.output,
        artifacts: event.artifacts,
        thoughts: event.thoughts,
        failure_reason: event.failure_reason,
        git_commit: event.git_commit,
      }
    case 'plan.done':
      return { type: 'WS_ACT_PLAN_DONE', total_steps: event.total_steps, all_ok: event.all_ok }
    case 'error':
      return { type: 'WS_ERROR', code: event.code, message: event.message }
    default:
      return null
  }
}

export class ActStreamClient {
  private ws: WebSocket | null = null

  connect(
    sessionId: string,
    onEvent: (event: ActEvent) => void,
    onClose: () => void,
    onOpen?: () => void,
  ): void {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${window.location.host}/api/ws/sessions/${sessionId}/act`
    this.ws = new WebSocket(url)

    this.ws.onopen = () => onOpen?.()
    this.ws.onmessage = (e: MessageEvent) => {
      try {
        onEvent(JSON.parse(e.data as string) as ActEvent)
      } catch {
        // ignore malformed JSON
      }
    }
    this.ws.onclose = onClose
    this.ws.onerror = () => onClose()
  }

  sendExecute(): void {
    this.ws?.send(JSON.stringify({ type: 'execute' }))
  }

  close(): void {
    this.ws?.close()
    this.ws = null
  }
}
