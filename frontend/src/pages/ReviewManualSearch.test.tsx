import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ReviewManualSearch } from "@/pages/ReviewManualSearch";
import { ApiError } from "@/lib/api";

const mockGetReviewBundle = vi.fn();
const mockGetCapabilities = vi.fn();
const mockSearch = vi.fn();
const mockImport = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getReviewBundle: (...args: unknown[]) => mockGetReviewBundle(...args),
    getManualCandidateSearchCapabilities: (...args: unknown[]) =>
      mockGetCapabilities(...args),
    searchManualCandidates: (...args: unknown[]) => mockSearch(...args),
    importManualCandidate: (...args: unknown[]) => mockImport(...args),
  };
});

function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/reviews/7/search"]}>
        <Routes>
          <Route path="/reviews/:id/search" element={children} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("ReviewManualSearch", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetReviewBundle.mockResolvedValue({
      id: 7,
      current_revision: { candidate_source: null, candidate_ref: null },
    });
    mockGetCapabilities.mockResolvedValue([
      { provider: "musicbrainz", status: "available", supports_search: true },
    ]);
    mockSearch.mockResolvedValue({
      query: {
        title: "T",
        artist: null,
        album: null,
        year: null,
        duration_ms: null,
        isrc: null,
        providers: ["musicbrainz"],
        page: 0,
        page_size: 10,
      },
      candidates: [
        {
          source: "musicbrainz",
          ref_id: "release-1",
          album: "Album",
          album_artist: "Artist",
          year: 1999,
          track_count: 1,
          representative_title: "Track",
          representative_artist: "Artist",
        },
      ],
      provider_outcomes: [
        {
          provider: "musicbrainz",
          status: "results",
          result_count: 1,
          detail: null,
        },
      ],
      has_more: false,
    });
  });

  it("shows confirmation modal on 409 and retries with force", async () => {
    mockImport
      .mockImplementationOnce(() =>
        Promise.reject(
          new ApiError(
            409,
            "confirmation required: manual values for ['title'] would be overwritten",
          ),
        ),
      )
      .mockImplementationOnce(() =>
        Promise.resolve({ id: 7, current_revision: { id: 2 } }),
      );

    render(<ReviewManualSearch />, { wrapper: wrapper() });

    // wait for form to appear
    await screen.findByLabelText("Title");
    await waitFor(() => expect(mockGetCapabilities).toHaveBeenCalled());
    // fill title and search
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Track" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => expect(mockSearch).toHaveBeenCalled());
    await screen.findByText("Track");

    fireEvent.click(screen.getByText("Use this result"));

    await waitFor(() =>
      expect(mockImport).toHaveBeenCalledWith(
        7,
        "musicbrainz",
        "release-1",
        false,
      ),
    );
    // modal should appear
    await screen.findByText("Conferma sostituzione");
    expect(
      screen.getByText(/sovrascriverà valori modificati manualmente/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByText("Sovrascrivi valori manuali"));

    await waitFor(() =>
      expect(mockImport).toHaveBeenCalledWith(
        7,
        "musicbrainz",
        "release-1",
        true,
      ),
    );
    await waitFor(() => expect(mockImport).toHaveBeenCalledTimes(2));
  });
});
