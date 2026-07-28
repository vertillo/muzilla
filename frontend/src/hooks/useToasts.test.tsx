import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { pushToast, ToastProvider } from '@/hooks/useToasts'

describe('pushToast', () => {
  it('renders a toast pushed from outside the React tree once a ToastProvider is mounted', async () => {
    render(
      <ToastProvider>
        <div>app content</div>
      </ToastProvider>,
    )
    // docs/PLAN.md §12e step 5.4: App.tsx's MutationCache.onError calls
    // pushToast() directly — it isn't a component and can't use the
    // useToasts() hook — so this proves the ToastProvider's mount-time
    // registration (the useEffect wiring externalPush) actually works,
    // not just that useToasts() works from inside a component.
    pushToast({ tone: 'error', title: 'Action failed', description: 'network down' })
    expect(await screen.findByText('Action failed')).toBeInTheDocument()
    expect(screen.getByText('network down')).toBeInTheDocument()
  })

  it('is a silent no-op before any ToastProvider has mounted', () => {
    expect(() => pushToast({ tone: 'info', title: 'no provider yet' })).not.toThrow()
  })
})
