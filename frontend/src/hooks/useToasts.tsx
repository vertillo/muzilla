import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Toast, type ToastTone } from '@/components/ui'

interface ToastItem {
  id: number
  tone: ToastTone
  title: string
  description?: string
  action?: { label: string; onAction: () => void }
}

interface ToastContextValue {
  push: (toast: Omit<ToastItem, 'id'>) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

let nextId = 1

/** Lets module-scope query callbacks push through the mounted ToastProvider.
 * Calls made before registration are safely ignored. */
let externalPush: ToastContextValue['push'] = () => {}

export function pushToast(toast: Omit<ToastItem, 'id'>): void {
  externalPush(toast)
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])

  const push = useCallback((toast: Omit<ToastItem, 'id'>) => {
    const id = nextId++
    setToasts((prev) => [...prev, { ...toast, id }])
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 5000)
  }, [])

  const value = useMemo(() => ({ push }), [push])

  useEffect(() => {
    externalPush = push
    return () => {
      externalPush = () => {}
    }
  }, [push])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        style={{
          position: 'fixed',
          bottom: 'var(--space-5)',
          right: 'var(--space-5)',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          zIndex: 1000,
        }}
      >
        {toasts.map((t) => (
          <Toast
            key={t.id}
            tone={t.tone}
            title={t.title}
            description={t.description}
            action={t.action}
            onDismiss={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
          />
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToasts(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToasts must be used within a ToastProvider')
  return ctx
}
