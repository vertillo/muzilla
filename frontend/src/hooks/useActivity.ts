import {
  type InfiniteData,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import {
  cancelJob,
  listActivity,
  type ActivityItem,
  type ActivityPage,
  type ListActivityParams,
} from "@/lib/api";

export function useActivityList(
  params: Omit<ListActivityParams, "cursor"> = {},
) {
  return useInfiniteQuery({
    queryKey: ["activity", params.limit ?? 50, params.includeSystem ?? false],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => listActivity({ ...params, cursor: pageParam }),
    getNextPageParam: (lastPage: ActivityPage) =>
      lastPage.next_cursor ?? undefined,
    refetchInterval: 2000,
  });
}

export function useCancelActivityJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: number) => cancelJob(jobId),
    onSuccess: (detail, jobId) => {
      queryClient.setQueriesData<InfiniteData<ActivityPage>>(
        { queryKey: ["activity"] },
        (data) => {
          if (!data) return data;
          return {
            ...data,
            pages: data.pages.map((page) => ({
              ...page,
              items: page.items.map((item) =>
                item.job_id === jobId
                  ? {
                      ...item,
                      state: detail.state,
                      cancellable:
                        detail.state === "pending" ||
                        detail.state === "running",
                    }
                  : item,
              ),
            })),
          };
        },
      );
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ["activity"] });
        queryClient.invalidateQueries({ queryKey: ["jobs"] });
      }, 100);
    },
  });
}

export type { ActivityItem, ActivityPage };
