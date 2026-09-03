import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Badge,
  Button,
  EmptyState,
  ProgressBar,
  SkeletonRows,
  TableRow,
  type BadgeTone,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { useCancelActivityJob, useActivityList } from "@/hooks/useActivity";
import { useJob, useRetryFailedLyrics } from "@/hooks/useJobs";
import { useJobEvents } from "@/hooks/useJobEvents";
import { useImportSession } from "@/hooks/useImports";
import {
  useEnrichArt,
  useEnrichLyrics,
  useEnrichReplaygain,
} from "@/hooks/useEnrichment";
import { useCapabilities } from "@/hooks/useCapabilities";
import { useToasts } from "@/hooks/useToasts";
import { ApiError } from "@/lib/api";
import type { ActivityItem } from "@/lib/api";

const STATE_TONE: Record<string, BadgeTone> = {
  pending: "neutral",
  running: "accent",
  cancelling: "accent",
  succeeded: "added",
  failed: "removed",
  cancelled: "conflict",
};

const STATE_LABEL: Record<string, string> = {
  pending: "In attesa",
  running: "In corso",
  cancelling: "Annullamento…",
  succeeded: "Completata",
  failed: "Fallita",
  cancelled: "Annullata",
};

const KIND_LABEL: Record<ActivityItem["kind"], string> = {
  import: "Importazione",
  scan: "Scansione",
  apply: "Applicazione",
  undo: "Annullamento",
  duplicate_analysis: "Duplicati",
};

const KIND_TONE: Record<ActivityItem["kind"], BadgeTone> = {
  import: "accent",
  scan: "neutral",
  apply: "added",
  undo: "conflict",
  duplicate_analysis: "neutral",
};

function activityProgressPercent(item: ActivityItem): number | null {
  if (
    item.progress_total === null ||
    item.progress_total === 0 ||
    item.progress_current === null
  )
    return null;
  return Math.round((item.progress_current / item.progress_total) * 100);
}

function formatResultSummary(item: ActivityItem): string | null {
  const r = item.result;
  if (!r) return null;
  if (item.kind === "import") {
    const scanned = r.scanned;
    const added = r.added;
    if (typeof scanned === "number") {
      return `File scansionati: ${scanned}${typeof added === "number" ? ` • nuovi: ${added}` : ""}`;
    }
  }
  if (item.kind === "apply" && r.files && Array.isArray(r.files)) {
    return `File: ${(r.files as unknown[]).length} • stato: ${String(r.state ?? "")}`;
  }
  if (item.kind === "undo" && r.files && Array.isArray(r.files)) {
    return `File ripristinati: ${(r.files as unknown[]).length}`;
  }
  if (item.kind === "duplicate_analysis" && typeof r.groups === "number") {
    return `Gruppi duplicati: ${r.groups}`;
  }
  if (item.kind === "scan" && typeof r.scanned === "number") {
    return `Scansionati: ${r.scanned}`;
  }
  const keys = Object.keys(r).slice(0, 3).join(", ");
  return keys ? `Risultato: ${keys}` : null;
}

function ActivityDetailPanel({ item }: { item: ActivityItem }) {
  const navigate = useNavigate();
  const cancel = useCancelActivityJob();
  const jobEvents = useJobEvents(item.job_id ?? null);
  const jobDetail = useJob(item.job_id ?? null);
  const importDetail = useImportSession(item.import_session_id ?? null);
  const retryFailedLyrics = useRetryFailedLyrics();
  const isActive =
    item.state === "pending" ||
    item.state === "running" ||
    item.state === "cancelling";
  const retryableTrackIds = jobDetail.data?.result?.retryable_track_ids;
  const retryableCount = Array.isArray(retryableTrackIds)
    ? retryableTrackIds.length
    : 0;
  const showEnrichRetry = item.job_id !== null && retryableCount > 0;

  const tasks = importDetail.data?.tasks;
  const reviewLink = item.review_bundle_id
    ? `/reviews/${item.review_bundle_id}`
    : null;
  const importLink = item.import_session_id
    ? `/import/${item.import_session_id}`
    : null;

  return (
    <div className="py-4 px-5 border-b border-border-subtle bg-surface-raised">
      {/* Outcome / progress */}
      <div className="flex flex-col gap-2">
        {isActive && jobEvents.latestProgress && (
          <ProgressBar
            value={
              jobEvents.latestProgress.total
                ? Math.round(
                    (jobEvents.latestProgress.current /
                      jobEvents.latestProgress.total) *
                      100,
                  )
                : 0
            }
            label={
              jobEvents.latestProgress.message ??
              item.progress_message ??
              item.title
            }
          />
        )}
        {!isActive && item.progress_message && (
          <span className="text-xs text-text-secondary">
            {item.progress_message}
          </span>
        )}
        {formatResultSummary(item) && (
          <span className="text-sm text-text-secondary">
            {formatResultSummary(item)}
          </span>
        )}
        {item.error && (
          <div className="text-sm text-diff-removed">{item.error}</div>
        )}
        {importDetail.data && tasks && tasks.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-1">
            {tasks.map((t) => (
              <Badge
                key={t.stage}
                tone={
                  t.state === "done"
                    ? "added"
                    : t.state === "running"
                      ? "accent"
                      : t.state === "failed"
                        ? "removed"
                        : "neutral"
                }
              >
                {t.stage}: {t.state}
              </Badge>
            ))}
          </div>
        )}
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {importLink && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => navigate(importLink)}
          >
            Apri importazione
          </Button>
        )}
        {reviewLink && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => navigate(reviewLink)}
          >
            Apri revisione
          </Button>
        )}
        {item.cancellable &&
          item.job_id !== null &&
          (item.state === "pending" || item.state === "running") && (
            <Button
              size="sm"
              variant="ghost"
              disabled={cancel.isPending}
              onClick={() => cancel.mutate(item.job_id!)}
            >
              Annulla
            </Button>
          )}
        {item.state === "cancelling" && (
          <span className="text-sm text-text-muted py-1">
            Annullamento in corso…
          </span>
        )}
        {showEnrichRetry && (
          <Button
            size="sm"
            variant="ghost"
            disabled={retryFailedLyrics.isPending}
            onClick={() => retryFailedLyrics.mutate(item.job_id!)}
          >
            Retry failed ({retryableCount})
          </Button>
        )}
      </div>

      {/* Diagnostics */}
      <details className="mt-4 rounded border border-border-subtle bg-canvas">
        <summary className="cursor-pointer px-3 py-2 text-sm text-text-secondary">
          Diagnostica tecnica
        </summary>
        <div className="px-3 pb-3 pt-1 border-t border-border-subtle">
          <div className="text-xs font-mono text-text-muted space-y-1">
            <div>
              Attività: {item.id} • kind={item.kind} • job #{item.job_id ?? "—"}
            </div>
            {item.import_session_id && (
              <div>Import session #{item.import_session_id}</div>
            )}
            {item.review_bundle_id && (
              <div>Review bundle #{item.review_bundle_id}</div>
            )}
            {item.apply_run_id && <div>Apply run #{item.apply_run_id}</div>}
            {item.undo_run_id && <div>Undo run #{item.undo_run_id}</div>}
            {item.job_id && jobDetail.data && (
              <div>
                Correlazione job:{" "}
                {JSON.stringify({
                  payload: jobDetail.data.payload,
                  result: jobDetail.data.result,
                }).slice(0, 600)}
              </div>
            )}
          </div>
          <div className="mt-3">
            <div className="text-xs font-semibold text-text-secondary mb-1">
              Eventi job
            </div>
            <div className="flex flex-col gap-[4px] max-h-40 overflow-auto">
              {item.job_id === null ? (
                <span className="text-xs text-text-muted">
                  Nessun job associato.
                </span>
              ) : jobEvents.events.length === 0 ? (
                <span className="text-xs text-text-muted">No log events.</span>
              ) : (
                jobEvents.events
                  .filter((e) => e.kind === "log")
                  .map((e) => (
                    <div
                      key={e.seq}
                      className="text-xs font-mono text-text-secondary"
                    >
                      {String(
                        (e.payload as Record<string, unknown>).message ?? "",
                      )}
                    </div>
                  ))
              )}
            </div>
          </div>
          {item.result && (
            <div className="mt-3">
              <div className="text-xs font-semibold text-text-secondary mb-1">
                Risultato grezzo
              </div>
              <pre className="text-xs font-mono text-text-secondary whitespace-pre-wrap break-all max-h-40 overflow-auto bg-surface p-2 rounded">
                {JSON.stringify(item.result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      </details>
    </div>
  );
}

export function Jobs() {
  const [showSystem, setShowSystem] = useState(false);
  const {
    data,
    isLoading,
    isError,
    error,
    refetch,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useActivityList({ includeSystem: showSystem });
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const navigate = useNavigate();
  const toasts = useToasts();

  const enrichReplaygain = useEnrichReplaygain();
  const enrichArt = useEnrichArt();
  const enrichLyrics = useEnrichLyrics();
  const capabilities = useCapabilities();
  const replaygain = capabilities.data?.replaygain;
  const replaygainAvailable =
    !capabilities.isError && replaygain?.state === "available";

  function queueEnrichment(
    label: string,
    mutate: ReturnType<typeof useEnrichReplaygain>["mutate"],
  ) {
    mutate(undefined, {
      onSuccess: (job) =>
        toasts.push({
          tone: "info",
          title: `${label} queued (job #${job.job_id})`,
        }),
    });
  }

  const items: ActivityItem[] = data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="font-sans text-text-primary bg-canvas min-h-0">
      <PageHeader
        title="Attività"
        actions={
          <Button
            size="sm"
            variant="secondary"
            onClick={() => navigate("/import")}
          >
            New import
          </Button>
        }
      />

      <div className="flex items-center gap-3 py-3 px-5 border-b border-border-subtle">
        <Button
          size="sm"
          variant={showSystem ? "secondary" : "ghost"}
          onClick={() => setShowSystem((v) => !v)}
        >
          {showSystem
            ? "Nascondi attività di sistema"
            : "Mostra attività di sistema"}
        </Button>
        {showSystem && (
          <span className="text-xs text-text-muted">
            “Pulizia cronologia di annullamento” applica la policy configurata;
            non modifica file musicali.
          </span>
        )}
      </div>

      <details className="border-b border-border-subtle px-5 py-3">
        <summary className="cursor-pointer text-sm text-text-secondary">
          Azioni tecniche opzionali
        </summary>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <span className="text-xs text-text-muted">Enrichment:</span>
          <Button
            size="sm"
            variant="ghost"
            disabled={enrichReplaygain.isPending || !replaygainAvailable}
            aria-describedby="replaygain-capability-status"
            onClick={() =>
              queueEnrichment("ReplayGain", enrichReplaygain.mutate)
            }
          >
            ReplayGain
          </Button>
          <span
            id="replaygain-capability-status"
            className="text-xs text-text-muted"
            role="status"
          >
            {capabilities.isLoading
              ? "Checking ReplayGain…"
              : capabilities.isError
                ? "ReplayGain availability unknown"
                : !replaygainAvailable
                  ? `ReplayGain ${replaygain?.state ?? "unavailable"}: ${replaygain?.detail ?? "capability probe failed"}. Check runtime diagnostics and configuration, then retry.`
                  : ""}
          </span>
          <Button
            size="sm"
            variant="ghost"
            disabled={enrichArt.isPending}
            onClick={() => queueEnrichment("Album art", enrichArt.mutate)}
          >
            Album art
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={enrichLyrics.isPending}
            onClick={() => queueEnrichment("Lyrics", enrichLyrics.mutate)}
          >
            Lyrics
          </Button>
        </div>
      </details>

      {isLoading ? (
        <SkeletonRows />
      ) : isError ? (
        <div className="p-9">
          <EmptyState
            title="Impossibile caricare l'attività"
            description={
              error instanceof ApiError
                ? error.message
                : "The server returned an error."
            }
            action={<Button onClick={() => refetch()}>Riprova</Button>}
          />
        </div>
      ) : items.length === 0 ? (
        <div className="p-9">
          <EmptyState
            title="Nessuna attività"
            description="Scansioni, importazioni e applicazioni verranno mostrate qui — avvia un'importazione per vedere la prima attività."
            action={
              <Button onClick={() => navigate("/import")}>
                Avvia importazione
              </Button>
            }
          />
        </div>
      ) : (
        <div>
          {items.map((item: ActivityItem) => {
            const percent = activityProgressPercent(item);
            const isExpanded = expandedId === item.id;
            const stateLabel = STATE_LABEL[item.state] ?? item.state;
            return (
              <div key={item.id}>
                <div
                  role="button"
                  tabIndex={0}
                  aria-expanded={isExpanded}
                  onClick={() => setExpandedId(isExpanded ? null : item.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ")
                      setExpandedId(isExpanded ? null : item.id);
                  }}
                  className="focus-ring cursor-pointer"
                >
                  <TableRow>
                    <div className="w-[60px] font-mono text-text-muted shrink-0">
                      #{item.job_id ?? item.id.split(":")[1]}
                    </div>
                    <div className="w-[130px] shrink-0">
                      <Badge tone={KIND_TONE[item.kind]}>
                        {KIND_LABEL[item.kind]}
                      </Badge>
                    </div>
                    <div className="flex-1 min-w-0 truncate text-sm">
                      {item.title}
                    </div>
                    <div className="w-[120px] shrink-0">
                      <Badge tone={STATE_TONE[item.state] ?? "neutral"}>
                        {stateLabel}
                      </Badge>
                    </div>
                    <div className="flex-1 min-w-0 hidden sm:block">
                      {percent !== null ? (
                        <ProgressBar value={percent} />
                      ) : (
                        <span className="text-text-muted text-xs truncate">
                          {item.progress_message ?? ""}
                        </span>
                      )}
                    </div>
                    <div className="w-7 text-text-muted shrink-0">
                      {isExpanded ? "▾" : "▸"}
                    </div>
                  </TableRow>
                  <div className="sm:hidden px-5 pb-2">
                    {percent !== null ? (
                      <ProgressBar value={percent} />
                    ) : item.progress_message ? (
                      <span className="text-xs text-text-muted">
                        {item.progress_message}
                      </span>
                    ) : null}
                  </div>
                </div>
                {isExpanded && <ActivityDetailPanel item={item} />}
              </div>
            );
          })}
          {hasNextPage && (
            <div className="p-4 text-center">
              <Button
                size="sm"
                variant="ghost"
                disabled={isFetchingNextPage}
                onClick={() => fetchNextPage()}
              >
                {isFetchingNextPage ? "Caricamento…" : "Carica altro"}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
