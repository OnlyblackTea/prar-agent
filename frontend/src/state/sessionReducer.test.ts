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
