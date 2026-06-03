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
