import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuthStatus } from '@/hooks/useAuth'
import { useAuthStore } from '@/store/auth'

export function AuthGuard({ children }: { children: ReactNode }) {
  const { isLoading } = useAuthStatus()
  const status = useAuthStore((s) => s.status)
  const location = useLocation()

  if (isLoading || status === 'unknown') {
    return null
  }

  if (status === 'unauthenticated') {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />
  }

  return <>{children}</>
}
