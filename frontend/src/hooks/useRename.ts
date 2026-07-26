import { useMutation, useQueryClient } from '@tanstack/react-query'
import { previewPaths, renamePaths, type PathPreviewParams } from '@/lib/api'

export function usePreviewPaths() {
  return useMutation({
    mutationFn: (params: PathPreviewParams) => previewPaths(params),
  })
}

export function useRenamePaths() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (params: PathPreviewParams) => renamePaths(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['changesets'] })
    },
  })
}
