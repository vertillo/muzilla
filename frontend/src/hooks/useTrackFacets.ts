import { useQuery } from "@tanstack/react-query";
import { getTrackFacets, type GetTrackFacetsParams } from "@/lib/api";
import type { FacetValue } from "@/lib/types";

export type FacetKey = "missing" | "missing-art" | "unmatched" | "errored";

export interface FacetState {
  artist: string | null;
  album: string | null;
  genre: string | null;
  format: string | null;
  flags: Set<FacetKey>;
}

export const EMPTY_FACETS: FacetState = {
  artist: null,
  album: null,
  genre: null,
  format: null,
  flags: new Set(),
};

export interface FacetOptions {
  artists: string[];
  albums: string[];
  genres: string[];
  formats: string[];
}

export interface FacetOptionsWithCounts {
  artists: FacetValue[];
  albums: FacetValue[];
  genres: FacetValue[];
  formats: FacetValue[];
}

const EMPTY_OPTIONS: FacetOptions = {
  artists: [],
  albums: [],
  genres: [],
  formats: [],
};
const EMPTY_OPTIONS_WITH_COUNTS: FacetOptionsWithCounts = {
  artists: [],
  albums: [],
  genres: [],
  formats: [],
};

export function encodeFacetCursor(value: string): string {
  const raw = JSON.stringify(value);
  // urlsafe base64 without padding, mirrors Python urlsafe_b64encode
  return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function facetParams(
  q: string,
  facets: FacetState,
  facet_q?: string,
  limit?: number,
  cursor?: string,
): GetTrackFacetsParams {
  return {
    q: q || undefined,
    artist: facets.artist ?? undefined,
    album: facets.album ?? undefined,
    genre: facets.genre ?? undefined,
    format: facets.format ?? undefined,
    flags: facets.flags.size ? [...facets.flags] : undefined,
    facet_q: facet_q || undefined,
    limit,
    cursor: cursor || undefined,
  } as GetTrackFacetsParams;
}

/** Full facet result with counts, paginated and facet_q-searchable.
 *  Counts reflect server-filtered state (q + other facets), each facet
 *  excludes its own filter so alternatives remain visible. Returns error/retry
 *  so UI can distinguish failure from empty. Pagination is value-cursor/keyset only. */
export function useFacetsWithCounts(
  q: string,
  facets: FacetState,
  facet_q: string = "",
  limit: number = 100,
  cursor?: string,
  enabled: boolean = true,
): {
  data: FacetOptionsWithCounts;
  isError: boolean;
  error: unknown;
  refetch: () => void;
} {
  const { data, isError, error, refetch } = useQuery({
    queryKey: [
      "track-facets",
      q,
      facets.artist,
      facets.album,
      facets.genre,
      facets.format,
      [...facets.flags].sort().join(","),
      facet_q,
      limit,
      cursor,
    ],
    queryFn: () =>
      getTrackFacets(facetParams(q, facets, facet_q, limit, cursor)),
    staleTime: 30_000,
    enabled,
  });
  if (!data)
    return { data: EMPTY_OPTIONS_WITH_COUNTS, isError, error, refetch };
  return {
    data: {
      artists: data.artists,
      albums: data.albums,
      genres: data.genres,
      formats: data.formats,
    },
    isError,
    error,
    refetch,
  };
}

/** Per-field search hook for a single facet combobox — isolates
 *  facet_q per control while still sending the surrounding filter state
 *  so counts stay consistent. Returns error state for failed loads.
 *  Pagination is value-cursor/keyset only. */
export function useFacetField(
  field: "artist" | "album" | "genre" | "format",
  q: string,
  facets: FacetState,
  facetQuery: string,
  limit: number = 100,
  cursor?: string,
  enabled: boolean = true,
) {
  const { data, isError, error, refetch } = useFacetsWithCounts(
    q,
    facets,
    facetQuery,
    limit,
    cursor,
    enabled,
  );
  const fieldData =
    field === "artist"
      ? data.artists
      : field === "album"
        ? data.albums
        : field === "genre"
          ? data.genres
          : data.formats;
  return { data: fieldData, isError, error, refetch };
}

/** Backcompat helper for callers that only need value strings without counts. */
export function useFacetOptions(search: string): FacetOptions {
  const { data } = useQuery({
    queryKey: ["track-facets", search],
    queryFn: () => getTrackFacets(search ? { q: search } : {}),
    staleTime: 30_000,
  });
  if (!data) return EMPTY_OPTIONS;
  return {
    artists: data.artists.map((f) => f.value),
    albums: data.albums.map((f) => f.value),
    genres: data.genres.map((f) => f.value),
    formats: data.formats.map((f) => f.value),
  };
}

/** Deprecated client-side facet filter — kept for backwards-compat in tests
 *  but Catalog now relies solely on server filtering. Exists so existing
 *  unit tests importing applyFacets continue to compile. */
export function applyFacets<
  T extends {
    artist: string | null;
    album: string | null;
    genre: readonly string[];
    format: string | null;
    has_embedded_art: boolean;
    probe_error: string | null;
  },
>(tracks: T[], facets: FacetState): T[] {
  return tracks.filter((t) => {
    if (facets.artist && t.artist !== facets.artist) return false;
    if (facets.album && t.album !== facets.album) return false;
    if (facets.genre && !t.genre.includes(facets.genre as string)) return false;
    if (facets.format && t.format !== facets.format) return false;
    if (facets.flags.has("missing-art") && t.has_embedded_art) return false;
    if (facets.flags.has("unmatched") && t.album) return false;
    if (facets.flags.has("errored") && !t.probe_error) return false;
    // missing handled via base query exclusion in repo; client filter for completeness
    return true;
  });
}
