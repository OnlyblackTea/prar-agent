import { useReducer, useRef } from 'react'
import { PlanDocEditor } from './editor/PlanDocEditor'
import { planToTiptapDoc } from './editor/serializer'
import {
  sessionReducer,
  allBlockingAnswered,
  type SessionState,
} from './state/sessionReducer'
import { SessionContext, type SessionContextValue } from './state/SessionContext'
import { PlanStreamClient, type WSEvent } from './api/ws'
import { createSession, advanceToActing } from './api/sessions'
import { InitForm } from './components/InitForm'
import { ActionButton } from './components/ActionButton'
import { ErrorBanner } from './components/ErrorBanner'
import './App.css'

function eventToAction(event: WSEvent) {
  switch (event.type) {
    case 'plan.start':
      return { type: 'WS_PLAN_START' as const, title: event.title, summary: event.summary }
    case 'plan.node':
      return { type: 'WS_PLAN_NODE' as const, node: event.node }
    case 'plan.done':
      return { type: 'WS_PLAN_DONE' as const, totalNodes: event.total_nodes }
    case 'error':
      return { type: 'WS_ERROR' as const, code: event.code, message: event.message }
    default:
      return { type: 'WS_ERROR' as const, code: 'unknown', message: 'Unknown event' }
  }
}

export default function App() {
  const [state, dispatch] = useReducer(sessionReducer, { status: 'idle' } as SessionState)
  const clientRef = useRef<PlanStreamClient | null>(null)

  const handleStart = async (initRequest: string, adapterId: string) => {
    try {
      const session = await createSession({
        init_request: initRequest,
        adapter_id: adapterId,
      })
      dispatch({ type: 'START_SESSION', sessionId: session.id })

      const client = new PlanStreamClient()
      clientRef.current = client
      client.connect(
        session.id,
        (event) => dispatch(eventToAction(event)),
        () => { /* WS closed */ },
      )
      client.sendGenerate({
        init_request: initRequest,
        adapter_id: adapterId,
      })
    } catch (err) {
      dispatch({
        type: 'WS_ERROR',
        code: 'session_create_failed',
        message: err instanceof Error ? err.message : 'Unknown error',
      })
    }
  }

  const handleAdvance = async () => {
    if (state.status !== 'review') return
    try {
      await advanceToActing(state.sessionId)
      alert('进入 Action 阶段（M3 实现）')
    } catch {
      // error handled silently for now
    }
  }

  const handleReset = () => {
    clientRef.current?.close()
    clientRef.current = null
    dispatch({ type: 'RESET' })
  }

  const isBusy = state.status === 'connecting' || state.status === 'streaming'
  const hasPlan = state.status === 'streaming' || state.status === 'review'
  const sessionId =
    state.status !== 'idle' && state.status !== 'error' ? state.sessionId : ''
  const nodes =
    state.status === 'streaming' || state.status === 'review'
      ? state.plan.nodes
      : []

  const contextValue: SessionContextValue = { sessionId, dispatch }

  return (
    <SessionContext.Provider value={contextValue}>
      <div className="app">
        <header className="app-header">
          <h1>PRAR Agent</h1>
          <p className="subtitle">Plan / Review / Action / Review</p>
        </header>

        <main>
          <InitForm onSubmit={handleStart} disabled={isBusy} />

          {hasPlan && (
            <PlanDocEditor
              doc={planToTiptapDoc(
                state.status === 'streaming' || state.status === 'review'
                  ? state.plan
                  : { title: '', summary: '', nodes: [] },
              )}
            />
          )}

          {state.status === 'review' && (
            <ActionButton
              disabled={!allBlockingAnswered(nodes)}
              onClick={handleAdvance}
            />
          )}

          {state.status === 'error' && (
            <ErrorBanner
              code={state.code}
              message={state.message}
              onDismiss={handleReset}
            />
          )}
        </main>
      </div>
    </SessionContext.Provider>
  )
}
