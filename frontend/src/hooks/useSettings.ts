import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getSettings,
  previewTemplate,
  updateProviderSetting,
  updateStripFields,
  updateTemplates,
  type UpdateProviderSettingParams,
  type UpdateTemplatesParams,
} from '@/lib/api'

export function useSettings() {
  return useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
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
