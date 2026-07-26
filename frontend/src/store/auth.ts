import { create } from 'zustand'

interface AuthState {
  status: 'unknown' | 'checking' | 'authenticated' | 'unauthenticated'
  authEnabled: boolean
  setAuthenticated: (authenticated: boolean, authEnabled: boolean) => void
  setChecking: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  status: 'unknown',
  authEnabled: true,
  setChecking: () => set({ status: 'checking' }),
  setAuthenticated: (authenticated, authEnabled) =>
    set({ status: authenticated ? 'authenticated' : 'unauthenticated', authEnabled }),
}))
