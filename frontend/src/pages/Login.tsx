import { useState, type FormEvent } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Button, Input } from '@/components/ui'
import { useLogin } from '@/hooks/useAuth'
import { ApiError } from '@/lib/api'

export function Login() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const login = useLogin()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/catalog'

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    login.mutate(password, {
      onSuccess: () => navigate(from, { replace: true }),
      onError: (err) => {
        setError(err instanceof ApiError && err.status === 401 ? 'Incorrect password.' : 'Login failed.')
      },
    })
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg-canvas)',
      }}
    >
      <form
        onSubmit={handleSubmit}
        style={{
          width: 320,
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--space-4)',
          padding: 'var(--space-7)',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--border-subtle)',
          background: 'var(--bg-surface)',
        }}
      >
        <div
          style={{
            fontFamily: 'var(--font-sans)',
            fontSize: 'var(--text-lg-size)',
            fontWeight: 'var(--font-weight-semibold)',
            color: 'var(--text-primary)',
          }}
        >
          muzilla
        </div>
        <Input
          value={password}
          onChange={setPassword}
          placeholder="Password"
          type="password"
          error={error !== null}
        />
        {error && (
          <div style={{ fontFamily: 'var(--font-sans)', fontSize: 'var(--text-xs-size)', color: 'var(--diff-removed)' }}>
            {error}
          </div>
        )}
        <Button type="submit" disabled={login.isPending || password.length === 0}>
          {login.isPending ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
    </div>
  )
}
