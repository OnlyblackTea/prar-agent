import type { PlanNode } from '@/types/shared'

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
