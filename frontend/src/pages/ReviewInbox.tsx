import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  Badge,
  Button,
  EmptyState,
  SkeletonRows,
  ThumbnailTile,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { getReviewBundle, patchReviewOperationDecisions } from "@/lib/api";
import { useReviewInbox } from "@/hooks/useReviews";
import { useRecentImportSessions } from "@/hooks/useImports";
import { useToasts } from "@/hooks/useToasts";
import type { ReviewBundleSummary } from "@/lib/types";

const STATE_LABEL: Record<ReviewBundleSummary["state"], string> = {
  preparing: "In preparazione",
  ready: "Pronta",
  needs_attention: "Richiede attenzione",
  applying: "Applicazione in corso",
  applied: "Applicata",
  partially_applied: "Parzialmente applicata",
  failed: "Fallita",
  discarded: "Archiviata",
};

function toneForState(state: ReviewBundleSummary["state"]) {
  if (
    state === "needs_attention" ||
    state === "failed" ||
    state === "partially_applied"
  )
    return "conflict" as const;
  if (state === "ready" || state === "applied") return "added" as const;
  return "neutral" as const;
}

function updateSearch(
  current: URLSearchParams,
  setSearch: (next: URLSearchParams) => void,
  name: string,
  value: string,
) {
  const next = new URLSearchParams(current);
  if (value) next.set(name, value);
  else next.delete(name);
  setSearch(next);
}

export function ReviewInbox() {
  const [search, setSearch] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const toasts = useToasts();
  const filters = useMemo(
    () => ({
      q: search.get("q") ?? undefined,
      state: search.get("state")?.split(",").filter(Boolean),
      confidence: search.get("confidence") ?? undefined,
      issue: search.get("issue") ?? undefined,
      source: search.get("source") ?? undefined,
      session: search.get("session") ?? undefined,
    }),
    [search],
  );
  const inbox = useReviewInbox(filters);
  const recentSessions = useRecentImportSessions();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [undoSnapshot, setUndoSnapshot] = useState<Map<
    number,
    {
      revisionId: number;
      decisions: { operation_id: number; decision: string }[];
    }
  > | null>(null);

  const visibleItems = useMemo(
    () => inbox.data?.pages.flatMap((page) => page.items ?? []) ?? [],
    [inbox.data],
  );
  const visibleIds = useMemo(
    () => visibleItems.filter(Boolean).map((r) => r.id),
    [visibleItems],
  );
  const allVisibleSelected =
    visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));
  const selectedCount = selected.size;

  // Clear selection when filters change (avoid stale selection across filter change)
  useEffect(() => {
    setSelected(new Set());
  }, [search.toString()]);

  const singleReject = useMutation({
    mutationFn: async (reviewId: number) => {
      const review = await getReviewBundle(reviewId);
      return patchReviewOperationDecisions(
        reviewId,
        review.current_revision.id,
        review.current_revision.operations.map((operation) => ({
          operation_id: operation.id,
          decision: "rejected",
        })),
      );
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["reviews"] }),
  });

  const bulkReject = useMutation({
    mutationFn: async (ids: number[]) => {
      const prev = new Map<
        number,
        {
          revisionId: number;
          decisions: { operation_id: number; decision: string }[];
        }
      >();
      const results = await Promise.allSettled(
        ids.map(async (id) => {
          const bundle = await getReviewBundle(id);
          const prevDecisions = bundle.current_revision.operations.map(
            (op) => ({ operation_id: op.id, decision: op.decision }),
          );
          prev.set(id, {
            revisionId: bundle.current_revision.id,
            decisions: prevDecisions,
          });
          const nextDecisions = bundle.current_revision.operations.map(
            (op) => ({ operation_id: op.id, decision: "rejected" as const }),
          );
          return patchReviewOperationDecisions(
            id,
            bundle.current_revision.id,
            nextDecisions,
          );
        }),
      );
      const succeeded: number[] = [];
      const failed: { id: number; error: string }[] = [];
      results.forEach((r, idx) => {
        const id = ids[idx];
        if (r.status === "fulfilled") succeeded.push(id);
        else
          failed.push({
            id,
            error:
              r.reason instanceof Error ? r.reason.message : String(r.reason),
          });
      });
      return { succeeded, failed, prev };
    },
    onSuccess: ({ succeeded, failed, prev }) => {
      queryClient.invalidateQueries({ queryKey: ["reviews"] });
      setSelected((prevSet) => {
        const next = new Set(prevSet);
        succeeded.forEach((id) => next.delete(id));
        return next;
      });
      if (succeeded.length) {
        const snapshotForUndo = new Map<
          number,
          {
            revisionId: number;
            decisions: { operation_id: number; decision: string }[];
          }
        >();
        succeeded.forEach((id) => {
          const v = prev.get(id);
          if (v) snapshotForUndo.set(id, v);
        });
        setUndoSnapshot(snapshotForUndo);
        toasts.push({
          tone: "info",
          title: `Rifiutate ${succeeded.length} revisioni`,
          description: `${succeeded.length} proposte scartate — nessun file modificato.`,
          action: {
            label: "Annulla",
            onAction: () => {
              void (async () => {
                const entries = Array.from(snapshotForUndo.entries())
                const undoResults = await Promise.allSettled(
                  entries.map(async ([id, snap]) =>
                    patchReviewOperationDecisions(
                      id,
                      snap.revisionId,
                      snap.decisions as any,
                    ),
                  ),
                );
                const succeededUndo: number[] = []
                undoResults.forEach((r, idx) => {
                  if (r.status === 'fulfilled') succeededUndo.push(entries[idx][0])
                })
                const undoFailed = undoResults.filter((r) => r.status === 'rejected').length
                if (succeededUndo.length) {
                  setSelected((prevSet) => {
                    const next = new Set(prevSet)
                    succeededUndo.forEach((id) => next.add(id))
                    return next
                  })
                }
                queryClient.invalidateQueries({ queryKey: ["reviews"] });
                setUndoSnapshot(null);
                if (succeededUndo.length) {
                  toasts.push({
                    tone: "info",
                    title: `Annullate ${succeededUndo.length} revisioni`,
                    description: undoFailed ? `${undoFailed} non annullate` : undefined,
                  });
                } else if (undoFailed) {
                  toasts.push({
                    tone: "error",
                    title: "Annullamento fallito",
                    description: "Ricarica la lista e riprova.",
                  });
                }
              })();
            },
          },
        } as any);
      }
      if (failed.length) {
        toasts.push({
          tone: "error",
          title: `Rifiuto parziale: ${failed.length} falliti`,
          description: failed
            .map((f) => `#${f.id}: ${f.error}`)
            .join("; ")
            .slice(0, 300),
        });
      }
    },
    onError: (err: unknown) => {
      toasts.push({
        tone: "error",
        title: "Rifiuto bulk fallito",
        description: err instanceof Error ? err.message : String(err),
      });
    },
  });

  useEffect(() => {
    const anchor = location.hash.slice(1);
    if (!anchor || !inbox.data) return;
    requestAnimationFrame(() =>
      document.getElementById(anchor)?.scrollIntoView({ block: "nearest" }),
    );
  }, [inbox.data, location.hash]);

  const open = (id: number) => {
    const returnTo = `${location.pathname}${location.search}#review-${id}`;
    navigate(`/reviews/${id}?returnTo=${encodeURIComponent(returnTo)}`);
  };
  const clearFilters = () => setSearch(new URLSearchParams());
  const activeFilterCount = [
    "state",
    "confidence",
    "issue",
    "source",
    "session",
  ].filter((key) => search.has(key)).length;

  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const toggleSelectAllVisible = () => {
    if (allVisibleSelected) {
      setSelected((prev) => {
        const next = new Set(prev);
        visibleIds.forEach((id) => next.delete(id));
        return next;
      });
    } else {
      setSelected((prev) => {
        const next = new Set(prev);
        visibleIds.forEach((id) => next.add(id));
        return next;
      });
    }
  };

  return (
    <div className="min-h-0">
      <PageHeader title="Revisioni">
        <p className="mt-1 text-sm text-text-secondary">
          Controlla le proposte prima che qualsiasi file venga modificato.
        </p>
      </PageHeader>

      <div className="border-b border-border-subtle p-4 sm:p-5">
        <label htmlFor="review-search" className="block max-w-2xl text-sm font-medium text-text-primary">
          Cerca file, percorso, artista, titolo, provider o errore
          <input
            id="review-search"
            name="q"
            value={search.get("q") ?? ""}
            onChange={(event) =>
              updateSearch(search, setSearch, "q", event.target.value)
            }
            className="mt-2 min-h-11 w-full rounded-md border border-border-default bg-surface px-3 text-sm text-text-primary"
            placeholder="es. oxygen, /singles, MusicBrainz"
          />
        </label>
        <div
          className="mt-3 flex flex-wrap gap-2"
          aria-label="Filtri revisioni"
        >
          <label className="text-sm text-text-secondary">
            Stato
            <select
              value={search.get("state") ?? ""}
              onChange={(event) =>
                updateSearch(search, setSearch, "state", event.target.value)
              }
              className="ml-2 min-h-11 rounded-md border border-border-default bg-surface px-2 text-text-primary"
            >
              <option value="">Tutti</option>
              <option value="ready">Pronte</option>
              <option value="needs_attention">Richiede attenzione</option>
              <option value="preparing">In preparazione</option>
              <option value="failed">Fallite</option>
              <option value="applied,discarded">Applicate o archiviate</option>
            </select>
          </label>
          <label className="text-sm text-text-secondary">
            Confidenza
            <select
              value={search.get("confidence") ?? ""}
              onChange={(event) =>
                updateSearch(
                  search,
                  setSearch,
                  "confidence",
                  event.target.value,
                )
              }
              className="ml-2 min-h-11 rounded-md border border-border-default bg-surface px-2 text-text-primary"
            >
              <option value="">Tutte</option>
              <option value="high_confidence">High confidence</option>
              <option value="medium_confidence">Medium confidence</option>
              <option value="low_confidence">Low confidence</option>
              <option value="manual_selection">Manual selection</option>
              <option value="needs_attention">Needs attention</option>
              <option value="preparing">Preparing</option>
              <option value="not_scored">Not scored</option>
            </select>
          </label>
          <label className="text-sm text-text-secondary">
            Problema
            <select
              value={search.get("issue") ?? ""}
              onChange={(event) =>
                updateSearch(search, setSearch, "issue", event.target.value)
              }
              className="ml-2 min-h-11 rounded-md border border-border-default bg-surface px-2 text-text-primary"
            >
              <option value="">Tutti</option>
              <option value="review">Review</option>
              <option value="task">Enrichment</option>
              <option value="collision">Collisione percorso</option>
            </select>
          </label>
          <label className="text-sm text-text-secondary">
            Provider
            <select
              value={search.get("source") ?? ""}
              onChange={(event) =>
                updateSearch(search, setSearch, "source", event.target.value)
              }
              className="ml-2 min-h-11 rounded-md border border-border-default bg-surface px-2 text-text-primary"
            >
              <option value="">Tutti</option>
              <option value="musicbrainz">MusicBrainz</option>
              <option value="discogs">Discogs</option>
              <option value="deezer">Deezer</option>
            </select>
          </label>
          <label className="text-sm text-text-secondary">
            Sessione
            <select
              value={search.get("session") ?? ""}
              onChange={(event) =>
                updateSearch(search, setSearch, "session", event.target.value)
              }
              className="ml-2 min-h-11 rounded-md border border-border-default bg-surface px-2 text-text-primary"
            >
              <option value="">Tutte</option>
              <option value="none">Nessuna sessione</option>
              {(recentSessions.data?.items ?? []).map((s) => (
                <option key={s.id} value={String(s.id)}>
                  Import #{s.id}
                </option>
              ))}
            </select>
          </label>
          {activeFilterCount > 0 && (
            <Button variant="ghost" size="sm" onClick={clearFilters}>
              Cancella filtri
            </Button>
          )}
        </div>
        {activeFilterCount > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {Array.from(search.entries())
              .filter(([k]) =>
                [
                  "state",
                  "confidence",
                  "issue",
                  "source",
                  "session",
                  "q",
                ].includes(k),
              )
              .map(([k, v]) => (
                <span
                  key={`${k}-${v}`}
                  className="inline-flex items-center gap-1 rounded bg-surface-raised px-2 py-1 text-xs"
                >
                  {k}: {v}
                  <button
                    type="button"
                    aria-label={`Rimuovi filtro ${k}`}
                    onClick={() => updateSearch(search, setSearch, k, "")}
                    className="ml-1 text-text-secondary"
                  >
                    ×
                  </button>
                </span>
              ))}
          </div>
        )}
      </div>

      {(selectedCount > 0 || undoSnapshot) && (
        <div
          className="sticky top-0 z-10 flex flex-wrap items-center gap-3 border-b border-border-subtle bg-surface-raised p-3 sm:px-5"
          aria-live="polite"
        >
          {selectedCount > 0 ? (
            <>
              <span className="text-sm font-medium">
                {selectedCount} selezionate
              </span>
              <span className="text-xs text-text-secondary">
                Solo le revisioni visibili — seleziona manualmente le altre
                pagine.
              </span>
              <Button
                size="sm"
                variant="secondary"
                disabled={bulkReject.isPending}
                onClick={() => bulkReject.mutate(Array.from(selected))}
              >
                {bulkReject.isPending
                  ? "Rifiuto…"
                  : `Rifiuta selezionate (${selectedCount})`}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setSelected(new Set())}
              >
                Deseleziona
              </Button>
            </>
          ) : (
            <span className="text-sm text-text-secondary">
              Nessuna selezione
            </span>
          )}
          {undoSnapshot && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                void (async () => {
                  const entries = Array.from(undoSnapshot.entries());
                  const results = await Promise.allSettled(
                    entries.map(([id, snap]) =>
                      patchReviewOperationDecisions(
                        id,
                        snap.revisionId,
                        snap.decisions as any,
                      ),
                    ),
                  );
                  const succeededUndo: number[] = []
                  results.forEach((r, idx) => {
                    if (r.status === 'fulfilled') succeededUndo.push(entries[idx][0])
                  })
                  if (succeededUndo.length) {
                    setSelected((prevSet) => {
                      const next = new Set(prevSet)
                      succeededUndo.forEach((id) => next.add(id))
                      return next
                    })
                  }
                  queryClient.invalidateQueries({ queryKey: ["reviews"] });
                  setUndoSnapshot(null);
                  toasts.push({
                    tone: "info",
                    title: `Annullate ${succeededUndo.length} revisioni`,
                  } as any);
                })();
              }}
            >
              Annulla ultimo rifiuto
            </Button>
          )}
        </div>
      )}

      {inbox.isLoading ? (
        <SkeletonRows />
      ) : inbox.isError ? (
        <div className="p-6">
          <EmptyState
            title="Impossibile caricare le revisioni"
            action={<Button onClick={() => inbox.refetch()}>Riprova</Button>}
          />
        </div>
      ) : (inbox.data?.pages.flatMap((page) => page.items).length ?? 0) ===
        0 ? (
        <div className="p-6">
          <EmptyState
            title={
              activeFilterCount || search.has("q")
                ? "Nessuna revisione corrisponde ai filtri"
                : "Non ci sono revisioni da controllare"
            }
            description={
              activeFilterCount || search.has("q")
                ? "Modifica o cancella i filtri per vedere altre revisioni."
                : "Avvia una scansione o cerca corrispondenze da un file."
            }
            action={
              activeFilterCount || search.has("q") ? (
                <Button onClick={clearFilters}>Cancella filtri</Button>
              ) : undefined
            }
          />
        </div>
      ) : inbox.data ? (
        <div className="divide-y divide-border-subtle">
          <div className="flex items-center gap-3 px-4 py-3 text-sm text-text-secondary sm:px-5">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={allVisibleSelected}
                onChange={toggleSelectAllVisible}
                aria-label={
                  allVisibleSelected
                    ? "Deseleziona visibili"
                    : "Seleziona visibili"
                }
              />
              Seleziona visibili
            </label>
            <span>
              {inbox.data.pages[0]?.total ?? 0} revisioni nell’ordine di
              priorità
            </span>
          </div>
          {inbox.data.pages
            .flatMap((page) => page.items)
            .map((review) => (
              <article
                id={`review-${review.id}`}
                key={review.id}
                className="flex gap-3 p-4 sm:items-center sm:gap-4 sm:px-5"
              >
                <input
                  type="checkbox"
                  checked={selected.has(review.id)}
                  onChange={() => toggleSelect(review.id)}
                  aria-label={`Seleziona revisione ${review.filename ?? review.title}`}
                  className="mt-2 sm:mt-0"
                />
                <ThumbnailTile
                  src={review.cover_thumbnail_url ?? undefined}
                  label={
                    review.cover_thumbnail_url
                      ? "Cover proposta"
                      : "Nessuna cover proposta"
                  }
                />
                <button
                  type="button"
                  onClick={() => open(review.id)}
                  className="focus-ring min-w-0 flex-1 rounded-md p-1 text-left"
                  aria-label={`Apri revisione: ${review.filename ?? review.title}`}
                >
                  <div className="break-words font-medium text-text-primary">
                    {review.filename ?? review.title}
                  </div>
                  <div className="mt-1 break-all font-mono text-2xs text-text-secondary">
                    {review.path ?? "Percorso sorgente non disponibile"}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Badge tone={toneForState(review.state)}>
                      {STATE_LABEL[review.state]}
                    </Badge>
                    <Badge tone="neutral">
                      {review.confidence === null
                        ? review.confidence_label
                        : `${Math.round(review.confidence * 100)}%`}
                    </Badge>
                    {review.candidate_source && (
                      <Badge tone="neutral">{review.candidate_source}</Badge>
                    )}
                    {review.issues.map((item, index) => (
                      <Badge key={`${item.kind}-${index}`} tone="conflict">
                        Problema: {item.message}
                      </Badge>
                    ))}
                  </div>
                </button>
                <div className="flex shrink-0 flex-col gap-2 sm:flex-row">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => open(review.id)}
                  >
                    Apri
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={
                      singleReject.isPending || review.pending_operations === 0
                    }
                    onClick={() => singleReject.mutate(review.id)}
                  >
                    Rifiuta proposte
                  </Button>
                </div>
              </article>
            ))}
          {inbox.hasNextPage && (
            <div className="p-4 text-center">
              <Button
                variant="secondary"
                onClick={() => inbox.fetchNextPage()}
                disabled={inbox.isFetchingNextPage}
              >
                {inbox.isFetchingNextPage ? "Caricamento…" : "Carica altri"}
              </Button>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
