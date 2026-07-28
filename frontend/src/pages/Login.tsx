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
    <div className="min-h-screen flex items-center justify-center bg-canvas">
      <form
        onSubmit={handleSubmit}
        className="w-[320px] flex flex-col gap-4 p-7 rounded-lg border border-border-subtle bg-surface"
      >
        <div className="font-sans text-lg font-semibold text-text-primary">muzilla</div>
        <Input
          value={password}
          onChange={setPassword}
          placeholder="Password"
          type="password"
          error={error !== null}
        />
        {error && (
          <div className="font-sans text-xs" style={{ color: 'var(--diff-removed)' }}>
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
