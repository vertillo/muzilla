import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getImportConfig, getImportSession, resumeImport, startImport } from '@/lib/api'

export function useImportConfig() {
  return useQuery({
    queryKey: ['import-config'],
    queryFn: getImportConfig,
  })
}

export function useImportSession(id: number | null) {
  return useQuery({
    queryKey: ['import-session', id],
    queryFn: () => getImportSession(id as number),
    enabled: id !== null,
    // Cheap fallback poll for stage/state transitions between SSE
    // reconnects — useJobEvents drives the live per-tick UI.
    refetchInterval: 2000,
  })
}

export function useStartImport() {
  return useMutation({
    mutationFn: (libraryRoot: string) => startImport(libraryRoot),
    // ImportWizard.tsx already renders startImport.isError inline below
    // the library-root input — the global toast would be redundant.
    meta: { suppressErrorToast: true },
  })
}

export function useResumeImport(importSessionId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => resumeImport(importSessionId),
    onSuccess: () => {
      // resumeImport also returns only an ImportSessionSummary (no
      // tasks/changeset_ids) -- invalidate instead of seeding the
      // cache with an incompatible shape ImportReview.tsx expects to
      // be a full ImportSessionDetail.
      queryClient.invalidateQueries({ queryKey: ['import-session', importSessionId] })
    },
  })
}
