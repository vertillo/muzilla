import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Toast, type ToastTone } from '@/components/ui'

interface ToastItem {
  id: number
  tone: ToastTone
  title: string
  description?: string
}

interface ToastContextValue {
  push: (toast: Omit<ToastItem, 'id'>) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

let nextId = 1

/** Lets code outside React's tree (App.tsx's QueryClient, constructed at
 * module scope) push a toast — react-query's MutationCache.onError
 * (docs/PLAN.md §12e step 5.4: "surfaces mutation errors as toasts")
 * isn't a component and can't call the useToasts() hook directly.
 * ToastProvider registers the real push function on mount; before that
 * (there is no meaningful "before" in practice, since App.tsx mounts
 * ToastProvider immediately) this is a no-op rather than a throw, so an
 * error during the brief window before mount is silently dropped
 * instead of crashing the app over a toast. */
let externalPush: ToastContextValue['push'] = () => {}

export function pushToast(toast: Omit<ToastItem, 'id'>): void {
  externalPush(toast)
}

/** First real usage of the ported Toast component (docs/PLAN.md's
 * component gallery had it in isolation, but no screen used it) — job
 * completion/failure is what finally needs a toast-stacking mechanism. */
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
