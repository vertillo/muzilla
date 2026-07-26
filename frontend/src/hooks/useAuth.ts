import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { authStatus, login, logout } from '@/lib/api'
import { useAuthStore } from '@/store/auth'

export function useAuthStatus() {
  const setAuthenticated = useAuthStore((s) => s.setAuthenticated)
  return useQuery({
    queryKey: ['auth', 'status'],
    queryFn: async () => {
      const result = await authStatus()
      setAuthenticated(result.authenticated, result.enabled)
      return result
    },
    retry: false,
  })
}

export function useLogin() {
  const queryClient = useQueryClient()
  const setAuthenticated = useAuthStore((s) => s.setAuthenticated)
  return useMutation({
    mutationFn: login,
    onSuccess: (result) => {
      setAuthenticated(result.authenticated, result.enabled)
      queryClient.invalidateQueries({ queryKey: ['auth', 'status'] })
    },
  })
}

export function useLogout() {
  const queryClient = useQueryClient()
  const setAuthenticated = useAuthStore((s) => s.setAuthenticated)
  return useMutation({
    mutationFn: logout,
    onSuccess: (result) => {
      setAuthenticated(result.authenticated, result.enabled)
      queryClient.invalidateQueries({ queryKey: ['auth', 'status'] })
    },
  })
}
