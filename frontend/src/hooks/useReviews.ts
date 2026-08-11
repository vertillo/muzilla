import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getReviewBundle,
  getReviewNeighbors,
  listReviewBundles,
  patchReviewOperationDecisions,
  type ListReviewsParams,
} from '@/lib/api'
import type { ReviewOperationDecision } from '@/lib/types'

export function useReviewInbox(params: ListReviewsParams) {
  return useInfiniteQuery({
    queryKey: ['reviews', params],
    queryFn: ({ pageParam }) => listReviewBundles({ ...params, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  })
}

export function useReview(id: number | null) {
  return useQuery({
    queryKey: ['review', id],
    queryFn: () => getReviewBundle(id as number),
    enabled: id !== null,
  })
}

export function useReviewNeighbors(id: number | null, params: ListReviewsParams) {
  return useQuery({
    queryKey: ['review-neighbors', id, params],
    queryFn: () => getReviewNeighbors(id as number, params),
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
