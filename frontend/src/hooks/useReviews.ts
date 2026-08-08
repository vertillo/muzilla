import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getReviewBundle,
  listReviewBundles,
  patchReviewOperationDecisions,
  type ListReviewsParams,
} from '@/lib/api'
import type { ReviewOperationDecision } from '@/lib/types'

export function useReviewInbox(params: ListReviewsParams) {
  return useQuery({
    queryKey: ['reviews', params],
    queryFn: () => listReviewBundles(params),
  })
}

export function useReview(id: number | null) {
  return useQuery({
    queryKey: ['review', id],
    queryFn: () => getReviewBundle(id as number),
    enabled: id !== null,
  })
}

export function useReviewOperationDecisions(reviewId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ revisionId, decisions }: { revisionId: number, decisions: ReviewOperationDecision[] }) =>
      patchReviewOperationDecisions(reviewId, revisionId, decisions),
    onSuccess: (review) => {
      queryClient.setQueryData(['review', reviewId], review)
      queryClient.invalidateQueries({ queryKey: ['review', reviewId] })
      queryClient.invalidateQueries({ queryKey: ['reviews'] })
    },
    onError: () => queryClient.invalidateQueries({ queryKey: ['review', reviewId] }),
  })
}
