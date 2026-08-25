import type { PlanNode } from '@/types/shared'

// ===== Event types =====

export interface PlanStartEvent {
  type: 'plan.start'
  session_id: string
  title: string
  summary: string
}

export interface PlanNodeEvent {
  type: 'plan.node'
  index: number
  node: PlanNode
}

export interface PlanDoneEvent {
  type: 'plan.done'
  total_nodes: number
}

export interface ErrorEvent {
  type: 'error'
  code: string
  message: string
}

export type WSEvent = PlanStartEvent | PlanNodeEvent | PlanDoneEvent | ErrorEvent

// ===== WebSocket Client =====

export class PlanStreamClient {
  private ws: WebSocket | null = null

  connect(
    sessionId: string,
    onEvent: (event: WSEvent) => void,
    onClose: () => void,
    onOpen?: () => void,
  ): void {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${window.location.host}/api/ws/sessions/${sessionId}/plan`
    this.ws = new WebSocket(url)

    this.ws.onopen = () => onOpen?.()
    this.ws.onmessage = (e: MessageEvent) => {
      try {
        onEvent(JSON.parse(e.data as string) as WSEvent)
      } catch {
        // ignore malformed JSON
      }
    }
    this.ws.onclose = onClose
    this.ws.onerror = () => onClose()
  }

  sendGenerate(params: {
    init_request: string
    adapter_id: string
    ltm_recall?: string[]
    available_tools?: string[]
  }): void {
    this.ws?.send(JSON.stringify({ type: 'generate', ...params }))
  }

  close(): void {
    this.ws?.close()
    this.ws = null
  }
}
