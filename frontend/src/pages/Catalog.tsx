import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError } from "@/lib/api";
import {
  Badge,
  Button,
  EmptyState,
  Input,
  SkeletonRows,
} from "@/components/ui";
import { useTracks } from "@/hooks/useTracks";
import {
  useDetectDuplicates,
  useDismissDuplicate,
  useDuplicateGroups,
} from "@/hooks/useDuplicates";
import {
  encodeFacetCursor,
  useFacetField,
  type FacetKey,
  type FacetState,
} from "@/hooks/useTrackFacets";
import type { SortKey, TrackSummary } from "@/lib/types";

type CatalogColumnKey =
  | "title"
  | "artist"
  | "album"
  | "added"
  | "format"
  | "duration"
  | "status";

interface CatalogColumnDefinition {
  key: CatalogColumnKey;
  label: string;
  defaultWidth: number;
  minWidth: number;
  maxWidth: number;
  sortKey?: SortKey;
}

const CATALOG_COLUMNS: readonly CatalogColumnDefinition[] = [
  {
    key: "title",
    label: "Titolo",
    defaultWidth: 240,
    minWidth: 160,
    maxWidth: 420,
    sortKey: "title",
  },
  {
    key: "artist",
    label: "Artista",
    defaultWidth: 180,
    minWidth: 120,
    maxWidth: 320,
    sortKey: "artist",
  },
  {
    key: "album",
    label: "Album",
    defaultWidth: 220,
    minWidth: 140,
    maxWidth: 360,
    sortKey: "album",
  },
  {
    key: "added",
    label: "Aggiunto",
    defaultWidth: 120,
    minWidth: 96,
    maxWidth: 200,
    sortKey: "added",
  },
  {
    key: "format",
    label: "Formato",
    defaultWidth: 110,
    minWidth: 88,
    maxWidth: 180,
  },
  {
    key: "duration",
    label: "Durata",
    defaultWidth: 110,
    minWidth: 88,
    maxWidth: 180,
  },
  {
    key: "status",
    label: "Stato",
    defaultWidth: 220,
    minWidth: 188,
    maxWidth: 360,
  },
];

type CatalogColumnWidths = Record<CatalogColumnKey, number>;

const CATALOG_COLUMN_WIDTHS_STORAGE_KEY = "muzilla.catalog.column-widths";

function defaultCatalogColumnWidths(): CatalogColumnWidths {
  return Object.fromEntries(
    CATALOG_COLUMNS.map(({ key, defaultWidth }) => [key, defaultWidth]),
  ) as CatalogColumnWidths;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function catalogStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function clampCatalogColumnWidth(
  columnKey: CatalogColumnKey,
  width: number,
): number {
  const column = CATALOG_COLUMNS.find(({ key }) => key === columnKey);
  if (!column || !Number.isFinite(width)) return column?.defaultWidth ?? 0;
  return Math.min(
    column.maxWidth,
    Math.max(column.minWidth, Math.round(width)),
  );
}

function readCatalogColumnWidths(
  storage: Storage | null = catalogStorage(),
): CatalogColumnWidths {
  const widths = defaultCatalogColumnWidths();
  if (!storage) return widths;

  try {
    const raw = storage.getItem(CATALOG_COLUMN_WIDTHS_STORAGE_KEY);
    if (!raw) return widths;
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed)) return widths;
    for (const column of CATALOG_COLUMNS) {
      const saved = parsed[column.key];
      if (typeof saved === "number" && Number.isFinite(saved)) {
        widths[column.key] = clampCatalogColumnWidth(column.key, saved);
      }
    }
  } catch {
    // Private browsing, blocked storage, and malformed preferences use defaults.
  }
  return widths;
}

function persistCatalogColumnWidths(widths: CatalogColumnWidths): void {
  try {
    catalogStorage()?.setItem(
      CATALOG_COLUMN_WIDTHS_STORAGE_KEY,
      JSON.stringify(widths),
    );
  } catch {
    // Column layout is a preference; a storage failure must not affect the catalog.
  }
}

function clearCatalogColumnWidths(): void {
  try {
    catalogStorage()?.removeItem(CATALOG_COLUMN_WIDTHS_STORAGE_KEY);
  } catch {
    // Reset still applies in memory when storage is unavailable.
  }
}

function useCatalogColumnWidths() {
  const [widths, setWidths] = useState<CatalogColumnWidths>(() =>
    readCatalogColumnWidths(),
  );
  const widthsRef = useRef(widths);

  const resizeColumn = useCallback(
    (columnKey: CatalogColumnKey, width: number) => {
      const next = {
        ...widthsRef.current,
        [columnKey]: clampCatalogColumnWidth(columnKey, width),
      };
      widthsRef.current = next;
      setWidths(next);
      persistCatalogColumnWidths(next);
    },
    [],
  );

  const resetColumnWidths = useCallback(() => {
    const defaults = defaultCatalogColumnWidths();
    widthsRef.current = defaults;
    setWidths(defaults);
    clearCatalogColumnWidths();
  }, []);

  return { widths, resizeColumn, resetColumnWidths };
}

const FLAG_OPTIONS: { value: FacetKey; label: string }[] = [
  { value: "missing", label: "File mancante" },
  { value: "missing-art", label: "Senza cover" },
  { value: "unmatched", label: "Da identificare" },
  { value: "errored", label: "Errori di lettura" },
];

const VALID_FLAGS = new Set<FacetKey>([
  "missing",
  "missing-art",
  "unmatched",
  "errored",
]);
const VALID_SORTS: SortKey[] = ["title", "artist", "album", "added"];

function formatDuration(ms: number | null): string {
  if (ms === null) return "—";
  const seconds = Math.round(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function trackStatuses(track: TrackSummary): string[] {
  if (track.missing_since) return ["File mancante"];
  if (track.probe_error) return ["Errore di lettura"];
  const statuses = [];
  if (!track.has_embedded_art) statuses.push("Senza cover");
  if (!track.has_lyrics) statuses.push("Senza testo");
  if (!track.album) statuses.push("Da identificare");
  return statuses.length ? statuses : ["Pronto"];
}

function parseFacets(params: URLSearchParams): FacetState {
  const flagsRaw = params.get("flags");
  const flags = new Set<FacetKey>();
  if (flagsRaw) {
    for (const f of flagsRaw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)) {
      if (VALID_FLAGS.has(f as FacetKey)) flags.add(f as FacetKey);
    }
  }
  return {
    artist: params.get("artist") || null,
    album: params.get("album") || null,
    genre: params.get("genre") || null,
    format: params.get("format") || null,
    flags,
  };
}

function updateSearchParam(
  current: URLSearchParams,
  setSearch: (next: URLSearchParams, opts?: { replace?: boolean }) => void,
  name: string,
  value: string | null,
) {
  const next = new URLSearchParams(current);
  if (value === null || value === "") next.delete(name);
  else next.set(name, value);
  setSearch(next);
}

function setFlagParam(
  current: URLSearchParams,
  setSearch: (next: URLSearchParams, opts?: { replace?: boolean }) => void,
  flag: FacetKey,
) {
  const next = new URLSearchParams(current);
  const flagsRaw = next.get("flags");
  const flags = new Set<string>(
    flagsRaw ? flagsRaw.split(",").filter(Boolean) : [],
  );
  if (flags.has(flag)) flags.delete(flag);
  else flags.add(flag);
  if (flags.size) next.set("flags", [...flags].join(","));
  else next.delete("flags");
  setSearch(next);
}

// Accessible searchable facet combobox — debounced facet_q + cursor/offset pagination appended
function FacetCombobox({
  field,
  label,
  placeholder,
  q,
  facets,
  value,
  onChange,
}: {
  field: "artist" | "album" | "genre" | "format";
  label: string;
  placeholder: string;
  q: string;
  facets: FacetState;
  value: string | null;
  onChange: (value: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  const [accumulated, setAccumulated] = useState<
    { value: string; count: number }[]
  >([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const deferredQuery = useDeferredValue(query);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listId = `${field}-facet-listbox`;
  const inputId = `${field}-facet-input`;

  const flagsKey = [...facets.flags].sort().join(",");
  const PAGE_SIZE = 100;

  // Reset pagination when search changes
  useEffect(() => {
    setCursor(undefined);
    setAccumulated([]);
    setActiveIndex(0);
  }, [
    deferredQuery,
    q,
    facets.artist,
    facets.album,
    facets.genre,
    facets.format,
    flagsKey,
  ]);

  const {
    data: page,
    isError,
    refetch,
  } = useFacetField(field, q, facets, deferredQuery, PAGE_SIZE, cursor, open);
  // Append pages — first page replaces, subsequent appends via cursor
  useEffect(() => {
    if (!open) return;
    if (cursor === undefined) setAccumulated(page);
    else if (page.length) setAccumulated((prev) => [...prev, ...page]);
  }, [page, cursor, open]);

  const options = accumulated;
  const hasMore = page.length === PAGE_SIZE;

  const close = useCallback(() => {
    setOpen(false);
    // restore focus to trigger for keyboard users
    requestAnimationFrame(() => triggerRef.current?.focus());
  }, []);
  const openList = useCallback(() => setOpen(true), []);

  useEffect(() => {
    function onClickOutside(event: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      )
        close();
    }
    if (open) document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open, close]);

  const handleSelect = (val: string) => {
    onChange(val);
    close();
    setQuery("");
  };

  const handleClear = () => {
    onChange(null);
    close();
    setQuery("");
  };

  const handleKeyDown = (
    event: KeyboardEvent<HTMLInputElement | HTMLButtonElement>,
  ) => {
    if (!open && (event.key === "ArrowDown" || event.key === "Enter")) {
      event.preventDefault();
      openList();
      return;
    }
    if (!open) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, Math.max(options.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const opt = options[activeIndex];
      if (opt) handleSelect(opt.value);
      else if (value === null) handleClear();
    } else if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "Home") {
      event.preventDefault();
      setActiveIndex(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActiveIndex(Math.max(options.length - 1, 0));
    }
  };

  const selectedLabel = value ?? "";

  return (
    <div
      ref={containerRef}
      className="relative min-w-[12rem] flex-1 sm:flex-none sm:w-56"
    >
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => (open ? close() : openList())}
        onKeyDown={handleKeyDown}
        className="focus-ring flex w-full items-center justify-between gap-2 rounded-md border border-border-default bg-surface px-3 py-[6px] text-left text-sm"
      >
        <span className="min-w-0 truncate">
          {selectedLabel ? `${label}: ${selectedLabel}` : placeholder}
        </span>
        <span aria-hidden="true" className="shrink-0 text-text-muted">
          {open ? "▴" : "▾"}
        </span>
      </button>
      {open && (
        <div className="absolute left-0 right-0 z-20 mt-1 max-h-72 overflow-hidden rounded-md border border-border-default bg-surface shadow-md">
          <div className="p-2">
            <input
              id={inputId}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setOpen(true);
              }}
              onKeyDown={handleKeyDown}
              placeholder={`Cerca ${label.toLowerCase()}…`}
              aria-label={`Cerca ${label.toLowerCase()}`}
              aria-controls={listId}
              aria-activedescendant={
                options[activeIndex] ? `${field}-opt-${activeIndex}` : undefined
              }
              autoFocus
              className="focus-ring w-full rounded-md border border-border-default bg-surface px-3 py-2 text-sm"
            />
          </div>
          <ul
            id={listId}
            role="listbox"
            aria-label={label}
            className="max-h-48 overflow-auto p-1"
          >
            <li
              role="option"
              aria-selected={value === null}
              id={`${field}-opt-clear`}
              onClick={handleClear}
              className={`flex w-full items-center justify-between rounded px-3 py-2 text-left text-sm cursor-pointer ${value === null ? "bg-surface-raised font-medium" : "hover:bg-surface-raised"}`}
            >
              <span>Tutti gli {label.toLowerCase()}</span>
              {value === null && <span aria-hidden="true">✓</span>}
            </li>
            {isError ? (
              <li
                className="px-3 py-2 text-sm text-text-secondary"
                role="alert"
              >
                Impossibile caricare i filtri.{" "}
                <button
                  type="button"
                  className="underline"
                  onClick={() => refetch()}
                >
                  Riprova
                </button>
              </li>
            ) : options.length === 0 ? (
              <li
                className="px-3 py-2 text-sm text-text-secondary"
                role="status"
              >
                Nessun risultato{deferredQuery ? ` per “${deferredQuery}”` : ""}
                . Prova un termine diverso.
              </li>
            ) : (
              options.map((opt, idx) => {
                const isSelected = value === opt.value;
                const isActive = idx === activeIndex;
                return (
                  <li
                    key={opt.value}
                    role="option"
                    aria-selected={isSelected}
                    id={`${field}-opt-${idx}`}
                    onClick={() => handleSelect(opt.value)}
                    className={`flex w-full items-center justify-between rounded px-3 py-2 text-left text-sm cursor-pointer ${isActive ? "bg-surface-raised" : ""} ${isSelected ? "font-medium" : ""} hover:bg-surface-raised`}
                  >
                    <span className="min-w-0 truncate">{opt.value}</span>
                    <span className="ml-2 shrink-0 font-mono text-xs text-text-muted">
                      ({opt.count})
                    </span>
                    {isSelected && (
                      <span aria-hidden="true" className="ml-2">
                        ✓
                      </span>
                    )}
                  </li>
                );
              })
            )}
          </ul>
          {hasMore && !isError && (
            <div className="border-t border-border-subtle p-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  const last = accumulated[accumulated.length - 1]?.value;
                  if (last) setCursor(encodeFacetCursor(last));
                }}
              >
                Carica altri
              </Button>
              <span className="ml-2 text-xs text-text-muted">
                {options.length} mostrati
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ActiveChips({
  q,
  facets,
  searchParams,
  setSearchParams,
  dashboardStatus,
}: {
  q: string;
  facets: FacetState;
  searchParams: URLSearchParams;
  setSearchParams: (
    next: URLSearchParams,
    opts?: { replace?: boolean },
  ) => void;
  dashboardStatus: string | null;
}) {
  const chips: { key: string; label: string; onRemove: () => void }[] = [];
  if (q)
    chips.push({
      key: "q",
      label: `Ricerca: ${q}`,
      onRemove: () =>
        updateSearchParam(searchParams, setSearchParams, "q", null),
    });
  if (facets.artist)
    chips.push({
      key: "artist",
      label: `Artista: ${facets.artist}`,
      onRemove: () =>
        updateSearchParam(searchParams, setSearchParams, "artist", null),
    });
  if (facets.album)
    chips.push({
      key: "album",
      label: `Album: ${facets.album}`,
      onRemove: () =>
        updateSearchParam(searchParams, setSearchParams, "album", null),
    });
  if (facets.genre)
    chips.push({
      key: "genre",
      label: `Genere: ${facets.genre}`,
      onRemove: () =>
        updateSearchParam(searchParams, setSearchParams, "genre", null),
    });
  if (facets.format)
    chips.push({
      key: "format",
      label: `Formato: ${facets.format}`,
      onRemove: () =>
        updateSearchParam(searchParams, setSearchParams, "format", null),
    });
  for (const flag of facets.flags) {
    const label = FLAG_OPTIONS.find((o) => o.value === flag)?.label ?? flag;
    const isDashboardFlag =
      (dashboardStatus === "missing" && flag === "missing") ||
      (dashboardStatus === "errored" && flag === "errored");
    chips.push({
      key: `flag-${flag}`,
      label,
      onRemove: () => {
        if (isDashboardFlag) {
          const next = new URLSearchParams(searchParams);
          next.delete("status");
          // also toggle flag if present in flags
          const flagsRaw = next.get("flags");
          if (flagsRaw) {
            const flags = new Set(flagsRaw.split(",").filter(Boolean));
            flags.delete(flag);
            if (flags.size) next.set("flags", [...flags].join(","));
            else next.delete("flags");
          }
          setSearchParams(next);
        } else {
          setFlagParam(searchParams, setSearchParams, flag as FacetKey);
        }
      },
    });
  }
  if (chips.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2" aria-label="Filtri attivi">
      {chips.map((chip) => (
        <span
          key={chip.key}
          className="inline-flex items-center gap-1 rounded-full border border-border-default bg-surface-raised px-3 py-1 text-xs"
        >
          <span>{chip.label}</span>
          <button
            type="button"
            aria-label={`Rimuovi filtro ${chip.label}`}
            onClick={chip.onRemove}
            className="focus-ring ml-1 inline-flex h-5 w-5 items-center justify-center rounded-full text-text-secondary hover:bg-surface hover:text-text-primary"
          >
            <span aria-hidden="true">×</span>
          </button>
        </span>
      ))}
      <Button
        size="sm"
        variant="ghost"
        onClick={() =>
          setSearchParams(
            new URLSearchParams(
              [...searchParams.entries()].filter(
                ([k]) => k === "tool" || k === "status",
              ),
            ),
          )
        }
      >
        Cancella tutto
      </Button>
    </div>
  );
}

export function Catalog() {
  const [searchParams, setSearchParams] = useSearchParams();
  const dashboardStatus = searchParams.get("status");
  const duplicatesTool = searchParams.get("tool") === "duplicates";
  const q = searchParams.get("q") ?? "";
  const sort = (
    VALID_SORTS.includes(searchParams.get("sort") as SortKey)
      ? (searchParams.get("sort") as SortKey)
      : "title"
  ) as SortKey;
  const sortDirection = (searchParams.get("dir") === "desc" ? "desc" : "asc") as
    | "asc"
    | "desc";
  const facets = useMemo(() => parseFacets(searchParams), [searchParams]);
  const duplicates = useDuplicateGroups();
  const detectDuplicates = useDetectDuplicates();
  const dismissDuplicate = useDismissDuplicate();

  const effectiveFacets = useMemo(() => {
    if (dashboardStatus === "missing")
      return {
        ...facets,
        flags: new Set([...facets.flags, "missing" as FacetKey]),
      };
    if (dashboardStatus === "errored")
      return {
        ...facets,
        flags: new Set<FacetKey>([...facets.flags, "errored"]),
      };
    return facets;
  }, [dashboardStatus, facets]);

  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    isError,
    error,
    refetch,
  } = useTracks(q, sort, sortDirection, effectiveFacets);
  const tracks = data?.pages.flatMap((page) => page.items) ?? [];
  const total = data?.pages[0]?.total ?? 0;
  const filtered =
    q.trim() ||
    facets.artist ||
    facets.album ||
    facets.genre ||
    facets.format ||
    effectiveFacets.flags.size > 0;

  const setQ = (value: string) =>
    updateSearchParam(searchParams, setSearchParams, "q", value || null);
  const setFacet = (
    field: "artist" | "album" | "genre" | "format",
    value: string | null,
  ) => updateSearchParam(searchParams, setSearchParams, field, value);
  const setFlag = (flag: FacetKey) =>
    setFlagParam(searchParams, setSearchParams, flag);
  const clearAllFilters = () => {
    const keepTool = searchParams.get("tool");
    const keepStatus = searchParams.get("status");
    const next = new URLSearchParams();
    if (keepTool) next.set("tool", keepTool);
    if (keepStatus) next.set("status", keepStatus);
    setSearchParams(next);
  };
  const setSortFromHeader = (key: SortKey) => {
    const next = new URLSearchParams(searchParams);
    if (key === sort) next.set("dir", sortDirection === "asc" ? "desc" : "asc");
    else {
      next.set("sort", key);
      next.set("dir", "asc");
    }
    setSearchParams(next);
  };

  return (
    <div className="min-h-0 bg-canvas">
      <header className="border-b border-border-subtle p-4 sm:p-5">
        <h1 className="text-xl font-semibold">Catalogo</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Cerca e controlla i file indicizzati. Ogni modifica passa da una
          revisione.
        </p>
        <div className="mt-4 flex flex-wrap gap-3">
          <div className="min-w-[min(100%,22rem)] flex-1">
            <label htmlFor="catalog-search" className="sr-only">
              Cerca
            </label>
            <Input
              id="catalog-search"
              name="q"
              value={q}
              onChange={setQ}
              placeholder="Cerca titolo, artista, album o percorso…"
            />
          </div>
          <FacetCombobox
            field="artist"
            label="Artista"
            placeholder="Tutti gli artisti"
            q={q}
            facets={effectiveFacets}
            value={facets.artist}
            onChange={(v) => setFacet("artist", v)}
          />
          <FacetCombobox
            field="album"
            label="Album"
            placeholder="Tutti gli album"
            q={q}
            facets={effectiveFacets}
            value={facets.album}
            onChange={(v) => setFacet("album", v)}
          />
          <FacetCombobox
            field="genre"
            label="Genere"
            placeholder="Tutti i generi"
            q={q}
            facets={effectiveFacets}
            value={facets.genre}
            onChange={(v) => setFacet("genre", v)}
          />
          <FacetCombobox
            field="format"
            label="Formato"
            placeholder="Tutti i formati"
            q={q}
            facets={effectiveFacets}
            value={facets.format}
            onChange={(v) => setFacet("format", v)}
          />
        </div>
        <div className="mt-3 flex flex-wrap gap-2" aria-label="Filtri catalogo">
          {FLAG_OPTIONS.map((option) => (
            <Button
              key={option.value}
              size="sm"
              variant={
                effectiveFacets.flags.has(option.value) ? "secondary" : "ghost"
              }
              onClick={() => setFlag(option.value)}
              aria-pressed={effectiveFacets.flags.has(option.value)}
            >
              {option.label}
            </Button>
          ))}
          {filtered ? (
            <Button size="sm" variant="ghost" onClick={clearAllFilters}>
              Cancella filtri
            </Button>
          ) : null}
          <Link
            className="focus-ring self-center rounded px-2 text-xs text-accent-text"
            to="/catalog?tool=duplicates"
          >
            Possibili duplicati
          </Link>
          <span className="ml-auto self-center text-xs text-text-muted">
            {tracks.length} di {total} file
          </span>
        </div>
        {filtered && (
          <div className="mt-3">
            <ActiveChips
              q={q}
              facets={effectiveFacets}
              searchParams={searchParams}
              setSearchParams={setSearchParams}
              dashboardStatus={dashboardStatus}
            />
          </div>
        )}
      </header>

      {duplicatesTool ? (
        <section className="p-4 sm:p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold">Possibili duplicati</h2>
              <p className="mt-1 max-w-2xl text-sm text-text-secondary">
                L’evidenza confronta impronta AcoustID, recording ID
                MusicBrainz, durata e qualità del file. È una segnalazione:
                Muzilla non elimina nulla automaticamente.
              </p>
            </div>
            <Button
              disabled={detectDuplicates.isPending}
              onClick={() => detectDuplicates.mutate()}
            >
              {detectDuplicates.isPending
                ? "Analisi in coda…"
                : "Analizza duplicati"}
            </Button>
          </div>
          {duplicates.isLoading ? (
            <SkeletonRows />
          ) : duplicates.data?.items.length ? (
            <div className="mt-4 space-y-3">
              {duplicates.data.items.map((group) => {
                // SAFETY: evidence is JSON from domain/duplicate_evidence.py serialized via DB/models DuplicateGroup.evidence
                const evidence = group.evidence as unknown as { confidence?: number; confidence_label?: string; confidence_explanation?: string; duration?: { min_ms?: number; max_ms?: number; delta_ms?: number; delta_percent?: number }; is_uncertain?: boolean; is_false_positive_candidate?: boolean; reason?: string; quality?: Array<{ track_id: number; format?: string; bitrate?: number; duration_ms?: number }> } | null | undefined
                // SAFETY: confidence is optional Float column, fallback to evidence.confidence for legacy rows
                const confidence = group.confidence ?? (evidence as unknown as { confidence?: number } | null | undefined)?.confidence
                const label = evidence?.confidence_label ?? (confidence !== undefined && confidence !== null ? (confidence >= 0.75 ? 'alta' : confidence >= 0.5 ? 'media' : 'bassa') : undefined)
                const duration = evidence?.duration
                const quality = evidence?.quality
                return (
                <article
                  key={group.id}
                  className="rounded-md border border-border-subtle p-4"
                >
                  <h3 className="font-medium">Possibile duplicato{label ? ` · confidenza ${label}` : ''}{evidence?.is_false_positive_candidate ? ' · possibile falso positivo' : evidence?.is_uncertain ? ' · incerto' : ''}</h3>
                  <p className="mt-1 text-sm text-text-secondary">
                    Evidenza:{" "}
                    {group.basis === "acoustid"
                      ? "impronta AcoustID → recording ID MusicBrainz"
                      : group.basis}{" "}
                    · recording {group.mb_recording_id}
                    {confidence !== undefined && confidence !== null ? ` · confidenza ${(confidence * 100).toFixed(0)}%` : ''}
                  </p>
                  {evidence?.confidence_explanation ? <p className="mt-1 text-xs text-text-muted">{evidence.confidence_explanation}</p> : null}
                  {evidence?.reason ? <p className="mt-1 text-xs text-text-muted">{evidence.reason}</p> : null}
                  {duration ? <p className="mt-1 text-xs text-text-muted">Durate: {duration.min_ms ? formatDuration(duration.min_ms) : '—'} – {duration.max_ms ? formatDuration(duration.max_ms) : '—'}{duration.delta_ms ? ` (Δ ${formatDuration(duration.delta_ms)}${duration.delta_percent ? `, ${duration.delta_percent.toFixed(1)}%` : ''})` : ''}</p> : null}
                  {quality && quality.length ? <p className="mt-1 text-xs text-text-muted">Qualità: {quality.map((q) => `${q.format ?? '—'} ${q.bitrate ? `${q.bitrate} kbps` : ''} ${q.duration_ms ? formatDuration(q.duration_ms) : ''}`.trim()).join(' · ')}</p> : null}
                  <p className="mt-2 text-xs text-text-muted">Muzilla non elimina né sceglie automaticamente; verifica l'ascolto prima di ignorare un possibile falso positivo.</p>
                  <ul className="mt-3 space-y-2">
                    {group.tracks.map((item) => (
                      <li key={item.id}>
                        <Link
                          className="focus-ring rounded text-sm text-inherit"
                          to={`/catalog/${item.id}`}
                        >
                          {item.title ?? item.path}
                        </Link>
                        <span className="ml-2 text-xs text-text-muted">
                          {item.format ?? "formato sconosciuto"}
                          {item.bitrate ? ` · ${item.bitrate} kbps` : ""}
                          {item.duration_ms
                            ? ` · ${formatDuration(item.duration_ms)}`
                            : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <div className="mt-3 flex gap-2">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={dismissDuplicate.isPending}
                      onClick={() => dismissDuplicate.mutate(group.id)}
                    >
                      Ignora segnalazione (falso positivo)
                    </Button>
                  </div>
                </article>
              )
              })}
            </div>
          ) : (
            <EmptyState
              title="Nessun possibile duplicato"
              description="Avvia l’analisi dopo avere calcolato le impronte dei file."
            />
          )}
        </section>
      ) : isError ? (
        <div className="p-6">
          <EmptyState
            title="Impossibile caricare il catalogo"
            description={
              error instanceof ApiError
                ? error.message
                : "Il server ha restituito un errore."
            }
            action={<Button onClick={() => refetch()}>Riprova</Button>}
          />
        </div>
      ) : isLoading ? (
        <SkeletonRows />
      ) : tracks.length === 0 ? (
        <div className="p-6">
          <EmptyState
            title="Nessun file trovato"
            description={
              filtered || q
                ? "Nessun file corrisponde a ricerca e filtri correnti."
                : "Avvia una scansione per indicizzare la libreria."
            }
          />
        </div>
      ) : (
        <>
          <CatalogTable
            tracks={tracks}
            sort={sort}
            sortDirection={sortDirection}
            onSort={setSortFromHeader}
          />
          {hasNextPage ? (
            <div className="p-4 text-center">
              <Button
                variant="secondary"
                disabled={isFetchingNextPage}
                onClick={() => fetchNextPage()}
              >
                {isFetchingNextPage ? "Caricamento…" : "Carica altri file"}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

interface CatalogTableProps {
  tracks: TrackSummary[];
  sort: SortKey;
  sortDirection: "asc" | "desc";
  onSort: (key: SortKey) => void;
}

interface ColumnResizeHandleProps {
  column: CatalogColumnDefinition;
  width: number;
  onResize: (columnKey: CatalogColumnKey, width: number) => void;
}

const KEYBOARD_RESIZE_STEP = 16;

function ColumnResizeHandle({
  column,
  width,
  onResize,
}: ColumnResizeHandleProps) {
  const pointer = useRef<{
    id: number;
    startX: number;
    startWidth: number;
  } | null>(null);

  const onPointerDown = (event: PointerEvent<HTMLSpanElement>) => {
    event.preventDefault();
    event.stopPropagation();
    pointer.current = {
      id: event.pointerId,
      startX: event.clientX,
      startWidth: width,
    };
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // Synthetic events and browsers without pointer capture still receive local moves.
    }
  };

  const onPointerMove = (event: PointerEvent<HTMLSpanElement>) => {
    const active = pointer.current;
    if (!active || active.id !== event.pointerId) return;
    event.preventDefault();
    onResize(column.key, active.startWidth + event.clientX - active.startX);
  };

  const onPointerEnd = (event: PointerEvent<HTMLSpanElement>) => {
    if (!pointer.current || pointer.current.id !== event.pointerId) return;
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // Pointer capture may not have been available for a synthetic event.
    }
    pointer.current = null;
  };

  const onKeyDown = (event: KeyboardEvent<HTMLSpanElement>) => {
    let nextWidth: number | null = null;
    if (event.key === "ArrowLeft") nextWidth = width - KEYBOARD_RESIZE_STEP;
    if (event.key === "ArrowRight") nextWidth = width + KEYBOARD_RESIZE_STEP;
    if (event.key === "Home") nextWidth = column.minWidth;
    if (event.key === "End") nextWidth = column.maxWidth;
    if (nextWidth === null) return;
    event.preventDefault();
    event.stopPropagation();
    onResize(column.key, nextWidth);
  };

  return (
    <span
      aria-label={`Ridimensiona colonna ${column.label}`}
      aria-orientation="vertical"
      aria-valuemax={column.maxWidth}
      aria-valuemin={column.minWidth}
      aria-valuenow={width}
      aria-valuetext={`${width} pixel di larghezza`}
      className="focus-ring absolute inset-y-0 right-0 z-10 w-7 cursor-col-resize touch-none rounded"
      data-column-key={column.key}
      onKeyDown={onKeyDown}
      onPointerCancel={onPointerEnd}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerEnd}
      role="separator"
      tabIndex={0}
      title="Trascina o usa le frecce per ridimensionare"
    >
      <span
        aria-hidden="true"
        className="absolute inset-y-2 left-1/2 w-px -translate-x-1/2 bg-border-default"
      />
    </span>
  );
}

export function CatalogTable({
  tracks,
  sort,
  sortDirection,
  onSort,
}: CatalogTableProps) {
  const { widths, resizeColumn, resetColumnWidths } = useCatalogColumnWidths();
  const tableWidth = CATALOG_COLUMNS.reduce(
    (total, column) => total + widths[column.key],
    0,
  );

  return (
    <section aria-label="Tabella catalogo">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle px-4 py-2 text-xs text-text-muted sm:px-5">
        <p id="catalog-column-help">
          Ridimensiona le colonne trascinando il divisore o usando le frecce
          della tastiera.
        </p>
        <Button size="sm" variant="ghost" onClick={resetColumnWidths}>
          Ripristina larghezze colonne
        </Button>
      </div>
      <div className="hidden overflow-x-auto md:block">
        <table
          aria-describedby="catalog-column-help"
          className="w-full min-w-[880px] table-fixed border-collapse text-left"
          style={{ minWidth: `${tableWidth}px`, width: `${tableWidth}px` }}
        >
          <caption className="sr-only">Catalogo dei file</caption>
          <colgroup>
            {CATALOG_COLUMNS.map((column) => (
              <col
                key={column.key}
                style={{
                  maxWidth: `${column.maxWidth}px`,
                  minWidth: `${column.minWidth}px`,
                  width: `${widths[column.key]}px`,
                }}
              />
            ))}
          </colgroup>
          <thead className="border-b border-border-subtle text-xs text-text-muted">
            <tr>
              {CATALOG_COLUMNS.map((column) => {
                const sortKey = column.sortKey;
                const isSorted = sortKey !== undefined && sort === sortKey;
                return (
                  <th
                    key={column.key}
                    aria-sort={
                      sortKey
                        ? isSorted
                          ? sortDirection === "asc"
                            ? "ascending"
                            : "descending"
                          : "none"
                        : undefined
                    }
                    className="relative p-0 font-medium"
                    style={{
                      maxWidth: `${column.maxWidth}px`,
                      minWidth: `${column.minWidth}px`,
                      width: `${widths[column.key]}px`,
                    }}
                    scope="col"
                  >
                    <div className="flex min-w-0 items-center p-3 pr-7">
                      {sortKey ? (
                        <button
                          type="button"
                          className="focus-ring min-w-0 rounded p-1 text-left"
                          onClick={() => onSort(sortKey)}
                          aria-label={`Ordina per ${column.label}`}
                        >
                          <span>{column.label}</span>
                          <span aria-hidden="true">
                            {isSorted
                              ? sortDirection === "asc"
                                ? " ↑"
                                : " ↓"
                              : ""}
                          </span>
                        </button>
                      ) : (
                        <span className="p-1">{column.label}</span>
                      )}
                    </div>
                    <ColumnResizeHandle
                      column={column}
                      width={widths[column.key]}
                      onResize={resizeColumn}
                    />
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {tracks.map((track) => (
              <DesktopRow key={track.id} track={track} widths={widths} />
            ))}
          </tbody>
        </table>
      </div>
      <div className="divide-y divide-border-subtle md:hidden">
        {tracks.map((track) => (
          <MobileRow key={track.id} track={track} />
        ))}
      </div>
    </section>
  );
}

function Statuses({ track }: { track: TrackSummary }) {
  return (
    <div className="flex flex-wrap gap-1">
      {trackStatuses(track).map((status) => (
        <Badge
          key={status}
          tone={
            status === "Pronto"
              ? "added"
              : status === "Errore di lettura" || status === "File mancante"
                ? "conflict"
                : "neutral"
          }
        >
          {status}
        </Badge>
      ))}
    </div>
  );
}

function DesktopRow({
  track,
  widths,
}: {
  track: TrackSummary;
  widths: CatalogColumnWidths;
}) {
  return (
    <tr className="border-b border-border-subtle align-top hover:bg-surface-raised">
      <td className="p-3" style={{ width: `${widths.title}px` }}>
        <Link
          className="focus-ring block break-words font-medium text-inherit"
          to={`/catalog/${track.id}`}
        >
          {track.title ?? track.filename}
        </Link>
        <span className="mt-1 block break-all font-mono text-2xs text-text-muted">
          {track.filename}
        </span>
      </td>
      <td
        className="p-3 break-words text-text-secondary"
        style={{ width: `${widths.artist}px` }}
      >
        {track.artist ?? "—"}
      </td>
      <td
        className="p-3 break-words text-text-secondary"
        style={{ width: `${widths.album}px` }}
      >
        {track.album ?? "—"}
      </td>
      <td
        className="p-3 text-text-secondary"
        style={{ width: `${widths.added}px` }}
      >
        {track.year ?? "—"}
      </td>
      <td
        className="p-3 text-text-secondary"
        style={{ width: `${widths.format}px` }}
      >
        {track.format ?? "—"}
      </td>
      <td
        className="p-3 font-mono text-text-secondary"
        style={{ width: `${widths.duration}px` }}
      >
        {formatDuration(track.duration_ms)}
      </td>
      <td className="p-3" style={{ width: `${widths.status}px` }}>
        <Statuses track={track} />
      </td>
    </tr>
  );
}

function MobileRow({ track }: { track: TrackSummary }) {
  return (
    <article className="p-4">
      <Link className="focus-ring block rounded" to={`/catalog/${track.id}`}>
        <h2 className="break-words font-medium">
          {track.title ?? track.filename}
        </h2>
        <p className="mt-1 break-words text-sm text-text-secondary">
          {track.artist ?? "Artista sconosciuto"}
          {track.album ? ` · ${track.album}` : ""}
        </p>
        <p className="mt-2 break-all font-mono text-2xs text-text-muted">
          {track.path}
        </p>
        <div className="mt-3 flex items-center justify-between gap-2">
          <Statuses track={track} />
          <span className="shrink-0 text-xs text-text-muted">
            {track.format ?? "—"} · {formatDuration(track.duration_ms)}
          </span>
        </div>
      </Link>
    </article>
  );
}
