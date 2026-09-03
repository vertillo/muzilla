import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { browseImport, getImportConfig, getImportSession, listImportSessions, previewImport, resumeImport, startImport } from '@/lib/api'

export function useImportConfig() {
  return useQuery({
    queryKey: ['import-config'],
    queryFn: getImportConfig,
    // Config fetch is on the critical path for the wizard; retry
    // more aggressively under full-suite DB contention so the
    // library_root read-only display appears reliably.
    retry: 5,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 5000),
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

export function useRecentImportSessions() {
  return useQuery({
    queryKey: ['import-sessions', 'recent'],
    queryFn: () => listImportSessions(),
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

export function useImportBrowse(path: string | null | undefined) {
  return useQuery({
    queryKey: ['import-browse', path ?? '__root__'],
    queryFn: () => browseImport(path ?? undefined),
    // Browsing is cheap; keep stale while navigating.
    staleTime: 10_000,
  })
}

export function useImportPreview(path: string | null) {
  return useQuery({
    queryKey: ['import-preview', path],
    queryFn: () => previewImport(path as string),
    enabled: !!path,
    staleTime: 10_000,
  })
}
