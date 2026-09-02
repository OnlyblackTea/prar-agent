import { describe, expect, it } from 'vitest'
import { sessionReducer, allBlockingAnswered } from './sessionReducer'
import type { CommentResponse } from '@/types/shared'

const emptyComments: CommentResponse[] = []

describe('sessionReducer', () => {
  it('START_SESSION transitions idle → connecting', () => {
    const state = sessionReducer(
      { status: 'idle' },
      { type: 'START_SESSION', sessionId: 'sid', planVersion: 0 },
    )
    expect(state).toEqual({ status: 'connecting', sessionId: 'sid', planVersion: 0 })
  })

  it('WS_PLAN_START transitions connecting → streaming with plan init', () => {
    const state = sessionReducer(
      { status: 'connecting', sessionId: 'sid', planVersion: 0 },
      { type: 'WS_PLAN_START', title: 'Test Plan', summary: 'A summary' },
    )
    expect(state).toMatchObject({
      status: 'streaming',
      sessionId: 'sid',
      plan: { title: 'Test Plan', summary: 'A summary', nodes: [] },
    })
  })

  it('WS_PLAN_NODE appends node to plan', () => {
    const node = { type: 'paragraph' as const, text: 'hello' }
    const state = sessionReducer(
      {
        status: 'streaming',
        sessionId: 'sid',
        planVersion: 0,
        plan: { title: 'T', summary: 'S', nodes: [] },
      },
      { type: 'WS_PLAN_NODE', node },
    )
    expect(state.status).toBe('streaming')
    if (state.status === 'streaming') {
      expect(state.plan.nodes).toEqual([node])
    }
  })

  it('WS_PLAN_DONE transitions streaming → review and increments planVersion', () => {
    const state = sessionReducer(
      {
        status: 'streaming',
        sessionId: 'sid',
        planVersion: 0,
        plan: {
          title: 'T',
          summary: 'S',
          nodes: [
            {
              type: 'decision',
              id: 'dec_001',
              question: 'Q?',
              kind: 'single_choice',
              options: ['A', 'B'],
              answer: null,
              blocking: true,
            },
          ],
        },
      },
      { type: 'WS_PLAN_DONE', totalNodes: 1 },
    )
    expect(state.status).toBe('review')
    if (state.status === 'review') {
      expect(state.comments).toEqual([])
      // backend save_plan 把 current_plan_version 0→1，前端同步
      expect(state.planVersion).toBe(1)
    }
  })

  it('WS_PLAN_DONE increments planVersion from existing version (Task 12 case)', () => {
    const state = sessionReducer(
      {
        status: 'streaming',
        sessionId: 'sid',
        planVersion: 1,
        plan: { title: 'T', summary: 'S', nodes: [] },
      },
      { type: 'WS_PLAN_DONE', totalNodes: 0 },
    )
    expect(state.status).toBe('review')
    if (state.status === 'review') {
      expect(state.planVersion).toBe(2)
    }
  })

  it('ANSWER_DECISION updates target node answer', () => {
    const state = sessionReducer(
      {
        status: 'review',
        sessionId: 'sid',
        planVersion: 0,
        plan: {
          title: 'T',
          summary: 'S',
          nodes: [
            {
              type: 'decision',
              id: 'dec_001',
              question: 'Q?',
              kind: 'single_choice',
              options: ['A', 'B'],
              answer: null,
              blocking: true,
            },
            {
              type: 'decision',
              id: 'dec_002',
              question: 'Q2?',
              kind: 'single_choice',
              options: ['X', 'Y'],
              answer: null,
              blocking: false,
            },
          ],
        },
        comments: emptyComments,
      },
      { type: 'ANSWER_DECISION', id: 'dec_001', answer: 'A' },
    )
    expect(state.status).toBe('review')
    if (state.status === 'review') {
      const dec1 = state.plan.nodes.find(
        (n) => n.type === 'decision' && n.id === 'dec_001',
      ) as { answer: string | null } | undefined
      expect(dec1?.answer).toBe('A')
    }
  })

  it('LOAD_COMMENTS loads comment array in review', () => {
    const c: CommentResponse = {
      id: 'c1',
      session_id: 'sid',
      plan_version: 1,
      anchor_id: 'a1',
      quote: 'hi',
      quote_context: '',
      body: 'comment',
      resolved: false,
      created_at: '2025-01-01T00:00:00Z',
    }
    const state = sessionReducer(
      {
        status: 'review',
        sessionId: 'sid',
        planVersion: 1,
        plan: { title: 'T', summary: 'S', nodes: [] },
        comments: emptyComments,
      },
      { type: 'LOAD_COMMENTS', comments: [c] },
    )
    if (state.status === 'review') {
      expect(state.comments).toEqual([c])
    }
  })

  it('ADD_COMMENT appends comment in review', () => {
    const c: CommentResponse = {
      id: 'c1',
      session_id: 'sid',
      plan_version: 1,
      anchor_id: 'a1',
      quote: 'hi',
      quote_context: '',
      body: 'comment',
      resolved: false,
      created_at: '2025-01-01T00:00:00Z',
    }
    const state = sessionReducer(
      {
        status: 'review',
        sessionId: 'sid',
        planVersion: 1,
        plan: { title: 'T', summary: 'S', nodes: [] },
        comments: emptyComments,
      },
      { type: 'ADD_COMMENT', comment: c },
    )
    if (state.status === 'review') {
      expect(state.comments).toEqual([c])
    }
  })

  it('MERGE_COMPLETED replaces plan and clears comments in review', () => {
    const c: CommentResponse = {
      id: 'c1',
      session_id: 'sid',
      plan_version: 1,
      anchor_id: 'a1',
      quote: 'hi',
      quote_context: '',
      body: 'comment',
      resolved: true,
      created_at: '2025-01-01T00:00:00Z',
    }
    const newPlan = { title: 'T2', summary: 'S2', nodes: [] }
    const state = sessionReducer(
      {
        status: 'review',
        sessionId: 'sid',
        planVersion: 1,
        plan: { title: 'T', summary: 'S', nodes: [] },
        comments: [c],
      },
      { type: 'MERGE_COMPLETED', planVersion: 2, plan: newPlan },
    )
    expect(state.status).toBe('review')
    if (state.status === 'review') {
      expect(state.planVersion).toBe(2)
      expect(state.plan).toEqual(newPlan)
      expect(state.comments).toEqual([])
    }
  })

  it('MERGE_COMPLETED is ignored outside review', () => {
    const state = sessionReducer(
      { status: 'connecting', sessionId: 'sid', planVersion: 0 },
      {
        type: 'MERGE_COMPLETED',
        planVersion: 2,
        plan: { title: 'T2', summary: 'S2', nodes: [] },
      },
    )
    expect(state).toEqual({ status: 'connecting', sessionId: 'sid', planVersion: 0 })
  })

  it('WS_ERROR sets error status', () => {
    const state = sessionReducer(
      { status: 'idle' },
      { type: 'WS_ERROR', code: 'boom', message: 'something failed' },
    )
    expect(state).toEqual({ status: 'error', code: 'boom', message: 'something failed' })
  })

  it('RESET returns to idle', () => {
    const state = sessionReducer(
      { status: 'error', code: 'x', message: 'm' },
      { type: 'RESET' },
    )
    expect(state).toEqual({ status: 'idle' })
  })
})

describe('sessionReducer acting phase', () => {
  const reviewState = {
    status: 'review' as const,
    sessionId: 'sid',
    planVersion: 1,
    plan: { title: 'T', summary: 'S', nodes: [] },
    comments: emptyComments,
  }

  const stepStartAction = {
    type: 'WS_ACT_STEP_START' as const,
    index: 0,
    step_id: 's1',
    title: '安装依赖',
    tool: 'shell',
    tool_args: { command: 'npm install' },
  }

  const actingWithOneStep = () =>
    sessionReducer(
      sessionReducer(reviewState, { type: 'START_ACTING' }),
      stepStartAction,
    )

  it('START_ACTING transitions review → acting with empty run', () => {
    const state = sessionReducer(reviewState, { type: 'START_ACTING' })
    expect(state).toMatchObject({
      status: 'acting',
      sessionId: 'sid',
      planVersion: 1,
      run: { status: 'running', allOk: null, error: null, steps: [] },
    })
  })

  it('START_ACTING is ignored outside review', () => {
    const state = sessionReducer({ status: 'idle' }, { type: 'START_ACTING' })
    expect(state).toEqual({ status: 'idle' })
  })

  it('WS_ACT_STEP_START appends a running step with full initial fields', () => {
    const state = actingWithOneStep()
    if (state.status !== 'acting') throw new Error('expected acting')
    expect(state.run.steps).toHaveLength(1)
    expect(state.run.steps[0]).toEqual({
      index: 0,
      stepId: 's1',
      title: '安装依赖',
      tool: 'shell',
      toolArgs: { command: 'npm install' },
      status: 'running',
      stdout: '',
      output: '',
      exitCode: null,
      attempts: 1,
      artifacts: [],
      thoughts: [],
      failureReason: null,
      gitCommit: null,
    })
  })

  it('WS_ACT_STEP_START ignores duplicate step_id', () => {
    const state = actingWithOneStep()
    const next = sessionReducer(state, stepStartAction)
    expect(next).toBe(state)
  })

  it('WS_ACT_TOOL_STDOUT appends chunks in order to matching step', () => {
    let state = actingWithOneStep()
    state = sessionReducer(state, {
      type: 'WS_ACT_TOOL_STDOUT',
      step_id: 's1',
      chunk: 'hello\n',
    })
    state = sessionReducer(state, {
      type: 'WS_ACT_TOOL_STDOUT',
      step_id: 's1',
      chunk: 'world\n',
    })
    if (state.status !== 'acting') throw new Error('expected acting')
    expect(state.run.steps[0].stdout).toBe('hello\nworld\n')
  })

  it('WS_ACT_TOOL_STDOUT ignores unknown step_id', () => {
    const state = actingWithOneStep()
    const next = sessionReducer(state, {
      type: 'WS_ACT_TOOL_STDOUT',
      step_id: 'ghost',
      chunk: 'x',
    })
    expect(next).toBe(state)
  })

  it('WS_ACT_TOOL_EXIT records exit code', () => {
    const state = sessionReducer(actingWithOneStep(), {
      type: 'WS_ACT_TOOL_EXIT',
      step_id: 's1',
      exit_code: 0,
      ok: true,
    })
    if (state.status !== 'acting') throw new Error('expected acting')
    expect(state.run.steps[0].exitCode).toBe(0)
  })

  it('WS_ACT_STEP_DONE finalizes step as done', () => {
    let state = actingWithOneStep()
    state = sessionReducer(state, {
      type: 'WS_ACT_TOOL_STDOUT',
      step_id: 's1',
      chunk: 'streamed\n',
    })
    state = sessionReducer(state, {
      type: 'WS_ACT_STEP_DONE',
      step_id: 's1',
      ok: true,
      attempts: 1,
      output: 'streamed\n',
      artifacts: ['/sandbox/runs/x/a.txt'],
      thoughts: ['think 1'],
      failure_reason: null,
      git_commit: 'a1b2c3d4e5f6',
    })
    if (state.status !== 'acting') throw new Error('expected acting')
    expect(state.run.steps[0]).toMatchObject({
      status: 'done',
      stdout: 'streamed\n',
      attempts: 1,
      artifacts: ['/sandbox/runs/x/a.txt'],
      thoughts: ['think 1'],
      failureReason: null,
      gitCommit: 'a1b2c3d4e5f6',
    })
  })

  it('WS_ACT_STEP_DONE finalizes failed step with failure reason', () => {
    const state = sessionReducer(actingWithOneStep(), {
      type: 'WS_ACT_STEP_DONE',
      step_id: 's1',
      ok: false,
      attempts: 3,
      output: 'last observation',
      artifacts: [],
      thoughts: [],
      failure_reason: 'command exited 127',
      git_commit: null,
    })
    if (state.status !== 'acting') throw new Error('expected acting')
    expect(state.run.steps[0]).toMatchObject({
      status: 'failed',
      attempts: 3,
      output: 'last observation',
      failureReason: 'command exited 127',
      gitCommit: null,
    })
  })

  it('WS_ACT_PLAN_DONE marks run done with allOk', () => {
    const state = sessionReducer(actingWithOneStep(), {
      type: 'WS_ACT_PLAN_DONE',
      total_steps: 1,
      all_ok: true,
    })
    if (state.status !== 'acting') throw new Error('expected acting')
    expect(state.run.status).toBe('done')
    expect(state.run.allOk).toBe(true)
  })

  it('WS_ACT_PLAN_DONE with all_ok false keeps done with allOk false', () => {
    const state = sessionReducer(actingWithOneStep(), {
      type: 'WS_ACT_PLAN_DONE',
      total_steps: 1,
      all_ok: false,
    })
    if (state.status !== 'acting') throw new Error('expected acting')
    expect(state.run.status).toBe('done')
    expect(state.run.allOk).toBe(false)
  })

  it('WS_ERROR during acting fails run and keeps acting status', () => {
    const state = sessionReducer(actingWithOneStep(), {
      type: 'WS_ERROR',
      code: 'internal',
      message: 'boom',
    })
    expect(state.status).toBe('acting')
    if (state.status === 'acting') {
      expect(state.run.status).toBe('failed')
      expect(state.run.error).toBe('internal: boom')
      expect(state.run.steps).toHaveLength(1)
    }
  })

  it('WS_ACT_* is ignored outside acting', () => {
    const state = sessionReducer(reviewState, stepStartAction)
    expect(state).toBe(reviewState)
  })
})

describe('allBlockingAnswered', () => {
  it('returns false when some blocking unansweed', () => {
    const nodes = [
      {
        type: 'decision' as const,
        id: '1',
        question: 'Q',
        kind: 'single_choice' as const,
        options: ['A'],
        answer: null,
        blocking: true,
      },
    ]
    expect(allBlockingAnswered(nodes)).toBe(false)
  })

  it('returns true when all blocking answeed', () => {
    const nodes = [
      {
        type: 'decision' as const,
        id: '1',
        question: 'Q',
        kind: 'single_choice' as const,
        options: ['A'],
        answer: 'A',
        blocking: true,
      },
    ]
    expect(allBlockingAnswered(nodes)).toBe(true)
  })

  it('ignores non-blocking decisions', () => {
    const nodes = [
      {
        type: 'decision' as const,
        id: '1',
        question: 'Q',
        kind: 'single_choice' as const,
        options: ['A'],
        answer: null,
        blocking: false,
      },
    ]
    expect(allBlockingAnswered(nodes)).toBe(true)
  })
})
