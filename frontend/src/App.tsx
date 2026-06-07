import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import type { Editor } from '@tiptap/react'
import { PlanDocEditor, type SelectionSnapshot } from './editor/PlanDocEditor'
import { planToTiptapDoc } from './editor/serializer'
import { applyAnchorMark } from './editor/marks/AnchorMark'
import './editor/marks/anchor.css'
import {
  sessionReducer,
  allBlockingAnswered,
  type SessionState,
} from './state/sessionReducer'
import { SessionContext, type SessionContextValue } from './state/SessionContext'
import { PlanStreamClient, type WSEvent } from './api/ws'
import { createSession, advanceToActing } from './api/sessions'
import { createComment, listComments } from './api/comments'
import { InitForm } from './components/InitForm'
import { ActionButton } from './components/ActionButton'
import { ErrorBanner } from './components/ErrorBanner'
import { CommentThreadPanel } from './components/CommentThreadPanel'
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
  const editorRef = useRef<Editor | null>(null)
  const [pendingSel, setPendingSel] = useState<SelectionSnapshot | null>(null)

  // 进入 review 时拉评论
  useEffect(() => {
    if (state.status !== 'review') return
    listComments(state.sessionId, state.planVersion).then((comments) => {
      dispatch({ type: 'LOAD_COMMENTS', comments })
    }).catch(() => {
      // no comments yet — fine
    })
  }, [state.status])

  const handleStart = async (initRequest: string, adapterId: string) => {
    try {
      const session = await createSession({
        init_request: initRequest,
        adapter_id: adapterId,
      })
      dispatch({ type: 'START_SESSION', sessionId: session.id, planVersion: session.current_plan_version })

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

  // ===== Comment handlers =====

  const handleRequestAddComment = useCallback((sel: SelectionSnapshot) => {
    setPendingSel(sel)
  }, [])

  const handleSubmitComment = useCallback(async (body: string) => {
    if (!pendingSel || state.status !== 'review') return
    const anchor_id = crypto.randomUUID().replace(/-/g, '').slice(0, 16)
    const planVersion = state.planVersion
    try {
      const comment = await createComment(state.sessionId, {
        anchor_id,
        plan_version: planVersion,
        quote: pendingSel.quote,
        quote_context: pendingSel.quoteContext,
        body,
      })
      // 写入成功后落 Mark
      if (editorRef.current) {
        applyAnchorMark(editorRef.current, pendingSel.from, pendingSel.to, {
          anchor_id,
          resolved: false,
        })
      }
      dispatch({ type: 'ADD_COMMENT', comment })
      setPendingSel(null)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'comment_failed'
      dispatch({ type: 'WS_ERROR', code: 'comment_create_failed', message: msg })
    }
  }, [pendingSel, state])

  const handleJumpToAnchor = useCallback((anchorId: string) => {
    if (!editorRef.current) return
    const { doc } = editorRef.current.state
    let pos: { from: number; to: number } | null = null
    doc.descendants((node, p) => {
      if (!node.isText) return
      node.marks.forEach((m) => {
        if (m.type.name === 'anchor' && m.attrs.anchor_id === anchorId) {
          pos = { from: p, to: p + node.nodeSize }
        }
      })
    })
    if (pos) {
      editorRef.current.commands.setTextSelection(pos)
      editorRef.current.commands.scrollIntoView()
    }
  }, [])

  // ===== Derived state =====

  const isBusy = state.status === 'connecting' || state.status === 'streaming'
  const hasPlan = state.status === 'streaming' || state.status === 'review'
  const isReview = state.status === 'review'
  const sessionId =
    state.status !== 'idle' && state.status !== 'error' ? state.sessionId : ''
  const nodes =
    state.status === 'streaming' || state.status === 'review'
      ? state.plan.nodes
      : []
  const comments = isReview ? state.comments : []

  const contextValue: SessionContextValue = { sessionId, dispatch }

  return (
    <SessionContext.Provider value={contextValue}>
      <div className="app">
        <header className="app-header">
          <h1>PRAR Agent</h1>
          <p className="subtitle">Plan / Review / Action / Review</p>
        </header>

        <main className={isReview ? 'app-main-review' : ''}>
          <InitForm onSubmit={handleStart} disabled={isBusy} />

          {hasPlan && (
            <div className="review-layout">
              <div className="review-editor">
                <PlanDocEditor
                  doc={planToTiptapDoc(
                    state.status === 'streaming' || state.status === 'review'
                      ? state.plan
                      : { title: '', summary: '', nodes: [] },
                  )}
                  onRequestAddComment={isReview ? handleRequestAddComment : undefined}
                  editorRef={editorRef}
                />
              </div>
              {isReview && (
                <CommentThreadPanel
                  comments={comments}
                  pendingSelection={pendingSel}
                  onCancel={() => setPendingSel(null)}
                  onSubmit={handleSubmitComment}
                  onJumpToAnchor={handleJumpToAnchor}
                />
              )}
            </div>
          )}

          {isReview && (
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
