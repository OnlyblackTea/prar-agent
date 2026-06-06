import type { PlanNode, PlanDocument } from '@/types/shared'

// ===== State =====

export type SessionState =
  | { status: 'idle' }
  | { status: 'connecting'; sessionId: string }
  | { status: 'streaming'; sessionId: string; plan: PartialPlan }
  | { status: 'review'; sessionId: string; plan: PlanDocument }
  | { status: 'error'; code: string; message: string }

export interface PartialPlan {
  title: string
  summary: string
  nodes: PlanNode[]
}

// ===== Actions =====

export type SessionAction =
  | { type: 'START_SESSION'; sessionId: string }
  | { type: 'WS_PLAN_START'; title: string; summary: string }
  | { type: 'WS_PLAN_NODE'; node: PlanNode }
  | { type: 'WS_PLAN_DONE'; totalNodes: number }
  | { type: 'WS_ERROR'; code: string; message: string }
  | { type: 'ANSWER_DECISION'; id: string; answer: string }
  | { type: 'RESET' }

// ===== Reducer =====

export function sessionReducer(
  state: SessionState,
  action: SessionAction,
): SessionState {
  switch (action.type) {
    case 'START_SESSION':
      return { status: 'connecting', sessionId: action.sessionId }

    case 'WS_PLAN_START':
      if (state.status !== 'connecting') return state
      return {
        ...state,
        status: 'streaming',
        plan: { title: action.title, summary: action.summary, nodes: [] },
      }

    case 'WS_PLAN_NODE':
      if (state.status !== 'streaming') return state
      return {
        ...state,
        plan: {
          ...state.plan,
          nodes: [...state.plan.nodes, action.node],
        },
      }

    case 'WS_PLAN_DONE':
      if (state.status !== 'streaming') return state
      return {
        status: 'review',
        sessionId: state.sessionId,
        plan: state.plan as PlanDocument,
      }

    case 'ANSWER_DECISION':
      if (state.status !== 'review') return state
      return {
        ...state,
        plan: {
          ...state.plan,
          nodes: state.plan.nodes.map((n: PlanNode) =>
            n.type === 'decision' && n.id === action.id
              ? { ...n, answer: action.answer }
              : n,
          ),
        },
      }

    case 'WS_ERROR':
      return { status: 'error', code: action.code, message: action.message }

    case 'RESET':
      return { status: 'idle' }

    default:
      return state
  }
}

// ===== Helpers =====

export function allBlockingAnswered(nodes: PlanNode[]): boolean {
  for (const n of nodes) {
    if (n.type === 'decision' && n.blocking && n.answer === null) {
      return false
    }
  }
  return true
}
