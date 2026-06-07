export type PlanNode =
  | { type: 'heading'; level: 1 | 2 | 3; text: string }
  | { type: 'paragraph'; text: string }
  | {
      type: 'decision'
      id: string
      question: string
      kind: 'single_choice' | 'multi_choice'
      options: string[]
      answer: string | null
      blocking: boolean
    }
  | { type: 'glossary'; id: string; term: string; definition: string }
  | {
      type: 'step'
      id: string
      title: string
      description: string
      tool: string
      tool_args: Record<string, unknown>
      rerunnable: boolean
    }

export interface PlanDocument {
  title: string
  summary: string
  nodes: PlanNode[]
}

export interface CommentResponse {
  id: string
  session_id: string
  plan_version: number
  anchor_id: string
  quote: string
  quote_context: string
  body: string
  resolved: boolean
  created_at: string
}
