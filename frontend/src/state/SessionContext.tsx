import { createContext, useContext } from 'react'
import type { SessionAction } from './sessionReducer'

export interface SessionContextValue {
  sessionId: string
  dispatch: (action: SessionAction) => void
}

export const SessionContext = createContext<SessionContextValue | null>(null)

export function useSession(): SessionContextValue | null {
  return useContext(SessionContext)
}
