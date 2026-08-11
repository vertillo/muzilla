import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getReviewBundle,
  getReviewNeighbors,
  listReviewBundles,
  patchReviewOperationDecisions,
  chooseReviewCover,
  editReviewOperation,
  retryReviewTask,
  uploadReviewCover,
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
    refetchInterval: (query) => query.state.data?.state === 'applying' ? 500 : false,
  })
}

function refreshReview(queryClient: ReturnType<typeof useQueryClient>, reviewId: number, review: unknown) {
  queryClient.setQueryData(['review', reviewId], review)
  queryClient.invalidateQueries({ queryKey: ['review', reviewId] })
  queryClient.invalidateQueries({ queryKey: ['reviews'] })
}

export function useReviewCover(reviewId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ action, assetCandidateId }: { action: 'keep' | 'select' | 'remove'; assetCandidateId?: number }) =>
      chooseReviewCover(reviewId, action, assetCandidateId),
    onSuccess: (review) => refreshReview(queryClient, reviewId, review),
  })
}

export function useReviewCoverUpload(reviewId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => uploadReviewCover(reviewId, file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['review', reviewId] })
      queryClient.invalidateQueries({ queryKey: ['reviews'] })
    },
  })
}

export function useReviewTaskRetry(reviewId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (kind: string) => retryReviewTask(reviewId, kind),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['review', reviewId] })
      queryClient.invalidateQueries({ queryKey: ['reviews'] })
    },
  })
}

export function useReviewOperationEdit(reviewId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ operationId, edit }: { operationId: number; edit: Parameters<typeof editReviewOperation>[2] }) =>
      editReviewOperation(reviewId, operationId, edit),
    onSuccess: (review) => refreshReview(queryClient, reviewId, review),
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
