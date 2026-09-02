import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ActStreamClient, actEventToAction } from './act'

// ===== 项目首个 WebSocket mock 先例（jsdom 不实现 WebSocket）=====

interface FakeMessageEvent {
  data: string
}

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  url: string
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: FakeMessageEvent) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    this.onclose?.()
  }
}

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ActStreamClient', () => {
  it('connects to the /act WS URL', () => {
    const client = new ActStreamClient()
    client.connect('sid-1', () => {}, () => {})
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(FakeWebSocket.instances[0].url).toMatch(/\/api\/ws\/sessions\/sid-1\/act$/)
  })

  it('sends execute frame only after open', () => {
    const client = new ActStreamClient()
    client.connect('sid-1', () => {}, () => {}, () => client.sendExecute())
    const ws = FakeWebSocket.instances[0]
    expect(ws.sent).toEqual([])
    ws.onopen?.()
    expect(ws.sent).toEqual(['{"type":"execute"}'])
  })

  it('parses messages and forwards to onEvent', () => {
    const events: unknown[] = []
    const client = new ActStreamClient()
    client.connect('sid-1', (e) => events.push(e), () => {})
    const ws = FakeWebSocket.instances[0]
    ws.onmessage?.({
      data: JSON.stringify({
        type: 'step.start',
        index: 0,
        step_id: 's1',
        title: 't',
        tool: 'shell',
        tool_args: {},
      }),
    })
    expect(events).toHaveLength(1)
    expect(events[0]).toMatchObject({ type: 'step.start', step_id: 's1' })
  })

  it('ignores malformed JSON without throwing', () => {
    const events: unknown[] = []
    const client = new ActStreamClient()
    client.connect('sid-1', (e) => events.push(e), () => {})
    const ws = FakeWebSocket.instances[0]
    expect(() => ws.onmessage?.({ data: 'not-json' })).not.toThrow()
    expect(events).toHaveLength(0)
  })

  it('close() is idempotent', () => {
    const closed = vi.fn()
    const client = new ActStreamClient()
    client.connect('sid-1', () => {}, closed)
    client.close()
    expect(closed).toHaveBeenCalledTimes(1)
    client.close()
    expect(closed).toHaveBeenCalledTimes(1)
  })

  it('sendExecute after close is a no-op', () => {
    const client = new ActStreamClient()
    client.connect('sid-1', () => {}, () => {})
    const ws = FakeWebSocket.instances[0]
    client.close()
    const sentBefore = ws.sent.length
    client.sendExecute()
    expect(ws.sent.length).toBe(sentBefore)
  })
})

describe('actEventToAction', () => {
  it('maps step.start', () => {
    expect(
      actEventToAction({
        type: 'step.start',
        index: 2,
        step_id: 's1',
        title: 't',
        tool: 'shell',
        tool_args: { a: 1 },
      }),
    ).toEqual({
      type: 'WS_ACT_STEP_START',
      index: 2,
      step_id: 's1',
      title: 't',
      tool: 'shell',
      tool_args: { a: 1 },
    })
  })

  it('maps tool.stdout', () => {
    expect(actEventToAction({ type: 'tool.stdout', step_id: 's1', chunk: 'x' })).toEqual({
      type: 'WS_ACT_TOOL_STDOUT',
      step_id: 's1',
      chunk: 'x',
    })
  })

  it('maps tool.exit', () => {
    expect(actEventToAction({ type: 'tool.exit', step_id: 's1', exit_code: 1, ok: false })).toEqual({
      type: 'WS_ACT_TOOL_EXIT',
      step_id: 's1',
      exit_code: 1,
      ok: false,
    })
  })

  it('maps step.done with all record fields', () => {
    expect(
      actEventToAction({
        type: 'step.done',
        step_id: 's1',
        ok: true,
        attempts: 2,
        output: 'out',
        artifacts: ['/a.txt'],
        thoughts: ['t1'],
        failure_reason: null,
        git_commit: 'abc123',
      }),
    ).toEqual({
      type: 'WS_ACT_STEP_DONE',
      step_id: 's1',
      ok: true,
      attempts: 2,
      output: 'out',
      artifacts: ['/a.txt'],
      thoughts: ['t1'],
      failure_reason: null,
      git_commit: 'abc123',
    })
  })

  it('maps plan.done', () => {
    expect(actEventToAction({ type: 'plan.done', total_steps: 3, all_ok: true })).toEqual({
      type: 'WS_ACT_PLAN_DONE',
      total_steps: 3,
      all_ok: true,
    })
  })

  it('maps error → WS_ERROR', () => {
    expect(actEventToAction({ type: 'error', code: 'internal', message: 'boom' })).toEqual({
      type: 'WS_ERROR',
      code: 'internal',
      message: 'boom',
    })
  })

  it('returns null for unknown event type', () => {
    expect(actEventToAction({ type: 'nope' } as never)).toBeNull()
  })
})
