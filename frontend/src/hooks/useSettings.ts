import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getSettings,
  factoryReset,
  previewTemplate,
  resetCatalogAndActivity,
  resetMatchingSettings,
  updateEnrichmentSettings,
  updateMatchingSettings,
  updatePathsPolicy,
  updateProviderSetting,
  updateStripFields,
  updateTemplates,
  type UpdateEnrichmentParams,
  type UpdateMatchingParams,
  type UpdatePathsPolicyParams,
  type UpdateProviderSettingParams,
  type UpdateTemplatesParams,
} from '@/lib/api'

// A reset deletes the server state rendered by these four primary surfaces.
// Removing, rather than merely invalidating, prevents a navigation immediately
// after success from rendering data still inside the global staleTime window.
const RESET_OWNED_QUERY_KEYS = [
  ['dashboard-summary'],
  ['changesets'],
  ['jobs'],
  ['job'],
  ['provider-status'],
  ['tracks'],
  ['track'],
  ['track-facets'],
  ['duplicates'],
  ['settings'],
] as const

async function removeResetOwnedQueries(queryClient: ReturnType<typeof useQueryClient>) {
  for (const queryKey of RESET_OWNED_QUERY_KEYS) await queryClient.cancelQueries({ queryKey })
  for (const queryKey of RESET_OWNED_QUERY_KEYS) queryClient.removeQueries({ queryKey })
}

export function useSettings() {
  return useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  })
}

export function useResetCatalogAndActivity() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ confirmation, key }: { confirmation: 'RESET CATALOG AND ACTIVITY'; key: string }) =>
      resetCatalogAndActivity({ scope: 'catalog_and_activity', confirmation }, key),
    onSuccess: () => removeResetOwnedQueries(queryClient),
  })
}

export function useFactoryReset() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ password, key }: { password: string; key: string }) =>
      factoryReset(
        { scope: 'factory', confirmation: 'FACTORY RESET MUZILLA', password },
        key,
      ),
    onSuccess: () => removeResetOwnedQueries(queryClient),
  })
}

export function useUpdateProviderSetting() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ provider, params }: { provider: string; params: UpdateProviderSettingParams }) =>
      updateProviderSetting(provider, params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })
}

export function useUpdateTemplates() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (params: UpdateTemplatesParams) => updateTemplates(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })
}

export function useUpdateStripFields() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (fields: string[]) => updateStripFields(fields),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })
}

export function useUpdateEnrichment() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (params: UpdateEnrichmentParams) => updateEnrichmentSettings(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })
}

export function useUpdatePathsPolicy() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (params: UpdatePathsPolicyParams) => updatePathsPolicy(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })
}

export function useUpdateMatching() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (params: UpdateMatchingParams) => updateMatchingSettings(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })
}

export function useResetMatching() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => resetMatchingSettings(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })
}

/** Live template preview — deliberately a mutation, not a query keyed
 * on the template string: the user is typing, and a query would either
 * fire on every keystroke (with no debounce primitive already in this
 * codebase to reach for) or need one built just for this. A mutation
 * fired from an onChange/blur handler is a smaller diff and matches
 * how RenameTracks' existing preview-on-demand flow already works. */
export function usePreviewTemplate() {
  return useMutation({
    mutationFn: (template: string) => previewTemplate(template),
  })
}
