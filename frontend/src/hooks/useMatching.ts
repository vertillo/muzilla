import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

// Legacy ChangeSet candidate flow removed — ReviewBundle is the only review model.
// Stubs kept to preserve module shape for any lingering imports; they are not used.

export function useCandidates(scopeType: string, scopeId: number | null) {
  return useQuery({
    queryKey: ["candidates", scopeType, scopeId],
    queryFn: async () =>
      ({
        candidates: [],
        strong: false,
        ambiguous: false,
        band: "reject",
        auto_applicable: false,
        needs_confirmation: false,
      }) as unknown,
    enabled: false,
  });
}

export function useStageMatch(_scopeType: string, _scopeId: number | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (_: { source: string; refId: string }) => {
      throw new Error("stageTrackMatch removed: use ReviewBundle");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
    },
  });
}
