import type { PlanNode, PlanDocument, CommentResponse } from '@/types/shared'

// ===== State =====

export interface ActionStep {
  index: number
  stepId: string
  title: string
  tool: string
  toolArgs: Record<string, unknown>
  status: 'running' | 'done' | 'failed'
  stdout: string
  output: string
  exitCode: number | null
  attempts: number
  artifacts: string[]
  thoughts: string[]
  failureReason: string | null
  gitCommit: string | null
}

export interface ActionRun {
  status: 'running' | 'done' | 'failed'
  allOk: boolean | null
  error: string | null
  steps: ActionStep[]
}

export type SessionState =
  | { status: 'idle' }
  | { status: 'connecting'; sessionId: string; planVersion: number }
  | { status: 'streaming'; sessionId: string; planVersion: number; plan: PartialPlan }
  | { status: 'review'; sessionId: string; planVersion: number; plan: PlanDocument; comments: CommentResponse[] }
  | { status: 'acting'; sessionId: string; planVersion: number; plan: PlanDocument; run: ActionRun }
  | { status: 'error'; code: string; message: string }

export interface PartialPlan {
  title: string
  summary: string
  nodes: PlanNode[]
}

// ===== Actions =====

export type SessionAction =
  | { type: 'START_SESSION'; sessionId: string; planVersion: number }
  | { type: 'WS_PLAN_START'; title: string; summary: string }
  | { type: 'WS_PLAN_NODE'; node: PlanNode }
  | { type: 'WS_PLAN_DONE'; totalNodes: number }
  | { type: 'WS_ERROR'; code: string; message: string }
  | { type: 'ANSWER_DECISION'; id: string; answer: string }
  | { type: 'LOAD_COMMENTS'; comments: CommentResponse[] }
  | { type: 'ADD_COMMENT'; comment: CommentResponse }
  | { type: 'MERGE_COMPLETED'; planVersion: number; plan: PlanDocument }
  | { type: 'START_ACTING' }
  | { type: 'WS_ACT_STEP_START'; index: number; step_id: string; title: string; tool: string; tool_args: Record<string, unknown> }
  | { type: 'WS_ACT_TOOL_STDOUT'; step_id: string; chunk: string }
  | { type: 'WS_ACT_TOOL_EXIT'; step_id: string; exit_code: number; ok: boolean }
  | { type: 'WS_ACT_STEP_DONE'; step_id: string; ok: boolean; attempts: number; output: string; artifacts: string[]; thoughts: string[]; failure_reason: string | null; git_commit: string | null }
  | { type: 'WS_ACT_PLAN_DONE'; total_steps: number; all_ok: boolean }
  | { type: 'RESET' }

// ===== Reducer =====

export function sessionReducer(
  state: SessionState,
  action: SessionAction,
): SessionState {
  switch (action.type) {
    case 'START_SESSION':
      return {
        status: 'connecting',
        sessionId: action.sessionId,
        planVersion: action.planVersion,
      }

    case 'WS_PLAN_START':
      if (state.status !== 'connecting') return state
      return {
        status: 'streaming',
        sessionId: state.sessionId,
        planVersion: state.planVersion,
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
      // backend save_plan 把 current_plan_version+1，前端同步自增（首次 0→1，
      // 后续 Task 12 Review Merger 触发时 1→2）
      return {
        status: 'review',
        sessionId: state.sessionId,
        planVersion: state.planVersion + 1,
        plan: state.plan as PlanDocument,
        comments: [],
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

    case 'LOAD_COMMENTS':
      if (state.status !== 'review') return state
      return { ...state, comments: action.comments }

    case 'ADD_COMMENT':
      if (state.status !== 'review') return state
      return {
        ...state,
        comments: [...state.comments, action.comment],
      }

    case 'MERGE_COMPLETED':
      if (state.status !== 'review') return state
      // comments 清空，等 App 的 useEffect 自动 listComments(v{N+1}) 补
      return {
        ...state,
        planVersion: action.planVersion,
        plan: action.plan,
        comments: [],
      }

    case 'START_ACTING':
      if (state.status !== 'review') return state
      return {
        status: 'acting',
        sessionId: state.sessionId,
        planVersion: state.planVersion,
        plan: state.plan,
        run: { status: 'running', allOk: null, error: null, steps: [] },
      }

    case 'WS_ACT_STEP_START':
      if (state.status !== 'acting') return state
      if (state.run.steps.some((s) => s.stepId === action.step_id)) return state
      return {
        ...state,
        run: {
          ...state.run,
          steps: [
            ...state.run.steps,
            {
              index: action.index,
              stepId: action.step_id,
              title: action.title,
              tool: action.tool,
              toolArgs: action.tool_args,
              status: 'running',
              stdout: '',
              output: '',
              exitCode: null,
              attempts: 1,
              artifacts: [],
              thoughts: [],
              failureReason: null,
              gitCommit: null,
            },
          ],
        },
      }

    case 'WS_ACT_TOOL_STDOUT':
      if (state.status !== 'acting') return state
      if (!state.run.steps.some((s) => s.stepId === action.step_id)) return state
      return {
        ...state,
        run: {
          ...state.run,
          steps: state.run.steps.map((s) =>
            s.stepId === action.step_id
              ? { ...s, stdout: s.stdout + action.chunk }
              : s,
          ),
        },
      }

    case 'WS_ACT_TOOL_EXIT':
      if (state.status !== 'acting') return state
      if (!state.run.steps.some((s) => s.stepId === action.step_id)) return state
      return {
        ...state,
        run: {
          ...state.run,
          steps: state.run.steps.map((s) =>
            s.stepId === action.step_id ? { ...s, exitCode: action.exit_code } : s,
          ),
        },
      }

    case 'WS_ACT_STEP_DONE':
      if (state.status !== 'acting') return state
      if (!state.run.steps.some((s) => s.stepId === action.step_id)) return state
      return {
        ...state,
        run: {
          ...state.run,
          steps: state.run.steps.map((s) =>
            s.stepId === action.step_id
              ? {
                  ...s,
                  status: action.ok ? 'done' : 'failed',
                  output: action.output,
                  attempts: action.attempts,
                  artifacts: action.artifacts,
                  thoughts: action.thoughts,
                  failureReason: action.failure_reason,
                  gitCommit: action.git_commit,
                }
              : s,
          ),
        },
      }

    case 'WS_ACT_PLAN_DONE':
      if (state.status !== 'acting') return state
      return {
        ...state,
        run: { ...state.run, status: 'done', allOk: action.all_ok },
      }

    case 'WS_ERROR':
      if (state.status === 'acting') {
        return {
          ...state,
          run: {
            ...state.run,
            status: 'failed',
            error: `${action.code}: ${action.message}`,
          },
        }
      }
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
