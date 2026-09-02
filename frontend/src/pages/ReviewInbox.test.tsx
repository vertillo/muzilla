import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { ReviewInbox } from "@/pages/ReviewInbox";
import { ToastProvider } from "@/hooks/useToasts";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/reviews?q=source"]}>
          {children}
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}

describe("ReviewInbox", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders filename, full path, textual state, confidence and error without hover", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          items: [
            {
              id: 7,
              title: "Review source",
              state: "needs_attention",
              filename: "01-source.flac",
              path: "/music/incoming/01-source.flac",
              format: "flac",
              candidate_source: "musicbrainz",
              confidence: null,
              confidence_label: "Needs attention",
              cover_thumbnail_url: null,
              issues: [
                { kind: "task", message: "Lyrics temporarily unavailable" },
              ],
              accepted_operations: 0,
              pending_operations: 2,
              rejected_operations: 0,
            },
          ],
          total: 1,
        }),
      }),
    );

    render(<ReviewInbox />, { wrapper });

    await waitFor(() =>
      expect(screen.getByText("01-source.flac")).toBeInTheDocument(),
    );
    expect(
      screen.getByText("/music/incoming/01-source.flac"),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText("Richiede attenzione").at(-1),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Needs attention")[0]).toBeInTheDocument();
    expect(
      screen.getByText("Problema: Lyrics temporarily unavailable"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Apri revisione: 01-source.flac/ }),
    ).toBeInTheDocument();
  });

  it("archives a fully rejected review and sends the current revision token", async () => {
    let rejected = false;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/imports")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [], total: 0 }),
        });
      }
      if (url.startsWith("/api/reviews?")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: rejected
              ? []
              : [
                  {
                    id: 7,
                    title: "Review source",
                    state: "ready",
                    filename: "01-source.flac",
                    path: "/music/incoming/01-source.flac",
                    format: "flac",
                    candidate_source: "musicbrainz",
                    confidence: null,
                    confidence_label: "Not scored",
                    cover_thumbnail_url: null,
                    issues: [],
                    accepted_operations: 0,
                    pending_operations: 1,
                    rejected_operations: 0,
                  },
                ],
            total: rejected ? 0 : 1,
          }),
        });
      }
      if (url === "/api/reviews/7/operations" && init?.method === "PATCH") {
        rejected = true;
        return Promise.resolve({
          ok: true,
          json: async () => ({ state: "discarded" }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({
          id: 7,
          current_revision: {
            id: 3,
            operations: [{ id: 11, decision: "pending" }],
          },
        }),
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ReviewInbox />, { wrapper });
    await screen.findByRole("button", { name: "Rifiuta proposte" });
    fireEvent.click(screen.getByRole("button", { name: "Rifiuta proposte" }));

    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: /Apri revisione/ }),
      ).not.toBeInTheDocument(),
    );
    const patch = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
    );
    if (!patch) throw new Error("review decision request was not sent");
    expect(JSON.parse((patch[1] as RequestInit).body as string)).toEqual({
      revision_id: 3,
      decisions: [{ operation_id: 11, decision: "rejected" }],
    });
  });

  it("bulk reject affects only visibly selected reviews, previews count and undo restores", async () => {
    function bulkWrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      return (
        <QueryClientProvider client={client}>
          <ToastProvider>
            <MemoryRouter initialEntries={["/reviews"]}>
              {children}
            </MemoryRouter>
          </ToastProvider>
        </QueryClientProvider>
      );
    }
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/imports"))
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [], total: 0 }),
        });
      if (url === "/api/reviews" || url.startsWith("/api/reviews?")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [
              {
                id: 7,
                title: "Review 7",
                state: "ready",
                filename: "a.flac",
                path: "/music/a.flac",
                format: "flac",
                candidate_source: "musicbrainz",
                confidence: null,
                confidence_label: "Not scored",
                cover_thumbnail_url: null,
                issues: [],
                accepted_operations: 0,
                pending_operations: 1,
                rejected_operations: 0,
              },
              {
                id: 8,
                title: "Review 8",
                state: "ready",
                filename: "b.flac",
                path: "/music/b.flac",
                format: "flac",
                candidate_source: "discogs",
                confidence: null,
                confidence_label: "Not scored",
                cover_thumbnail_url: null,
                issues: [],
                accepted_operations: 0,
                pending_operations: 1,
                rejected_operations: 0,
              },
            ],
            total: 2,
          }),
        });
      }
      if (url.match(/^\/api\/reviews\/\d+$/)) {
        const id = Number(url.split("/").pop());
        return Promise.resolve({
          ok: true,
          json: async () => ({
            id,
            current_revision: {
              id: id * 10,
              operations: [{ id: id * 100, decision: "pending" }],
            },
          }),
        });
      }
      if (
        url.match(/^\/api\/reviews\/\d+\/operations$/) &&
        init?.method === "PATCH"
      ) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ state: "discarded" }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ReviewInbox />, { wrapper: bulkWrapper });
    await screen.findByText("a.flac");
    expect(
      screen.getByText("2 revisioni nell’ordine di priorità"),
    ).toBeInTheDocument();
    // initially no bulk bar
    expect(screen.queryByText(/selezionate/)).not.toBeInTheDocument();
    // select visibili should select both
    fireEvent.click(screen.getByLabelText("Seleziona visibili"));
    expect(await screen.findByText("2 selezionate")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Rifiuta selezionate (2)" }),
    ).toBeInTheDocument();
    // preview count
    expect(screen.getByText(/Solo le revisioni visibili/)).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Rifiuta selezionate (2)" }),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(
          ([u, i]) =>
            String(u).endsWith("/operations") &&
            (i as RequestInit)?.method === "PATCH",
        ).length,
      ).toBe(2),
    );
    // after success, undo affordance appears (inline button + toast)
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Annulla ultimo rifiuto" }),
      ).toBeInTheDocument(),
    );
    // selection cleared for succeeded
    expect(screen.queryByText("2 selezionate")).not.toBeInTheDocument();
    // undo restores selection/list state
    const patchBeforeUndo = fetchMock.mock.calls.length;
    fireEvent.click(
      screen.getByRole("button", { name: "Annulla ultimo rifiuto" }),
    );
    await waitFor(() =>
      expect(fetchMock.mock.calls.length).toBeGreaterThan(patchBeforeUndo),
    );
    const undoPatches = fetchMock.mock.calls
      .slice(patchBeforeUndo)
      .filter(([u]) => String(u).endsWith("/operations"));
    expect(undoPatches.length).toBe(2);
    // undo must restore prior successful selection (F1)
    await waitFor(() => expect(screen.getByText("2 selezionate")).toBeInTheDocument());
    // verify never targeted all rows implicitly: only 2 PATCH for 2 selected, no wildcard
    const bulkPatches = fetchMock.mock.calls.filter(
      ([u, i]) =>
        String(u).includes("/operations") &&
        (i as RequestInit)?.method === "PATCH",
    );
    expect(bulkPatches.length).toBe(4); // 2 reject + 2 undo
  });

  it("bulk partial failure keeps failed selection and reports", async () => {
    function bulkWrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      return (
        <QueryClientProvider client={client}>
          <ToastProvider>
            <MemoryRouter initialEntries={["/reviews"]}>
              {children}
            </MemoryRouter>
          </ToastProvider>
        </QueryClientProvider>
      );
    }
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/imports"))
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [], total: 0 }),
        });
      if (url === "/api/reviews" || url.startsWith("/api/reviews?")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [
              {
                id: 7,
                title: "Review 7",
                state: "ready",
                filename: "a.flac",
                path: "/music/a.flac",
                format: "flac",
                candidate_source: "musicbrainz",
                confidence: null,
                confidence_label: "Not scored",
                cover_thumbnail_url: null,
                issues: [],
                accepted_operations: 0,
                pending_operations: 1,
                rejected_operations: 0,
              },
              {
                id: 8,
                title: "Review 8",
                state: "ready",
                filename: "b.flac",
                path: "/music/b.flac",
                format: "flac",
                candidate_source: "discogs",
                confidence: null,
                confidence_label: "Not scored",
                cover_thumbnail_url: null,
                issues: [],
                accepted_operations: 0,
                pending_operations: 1,
                rejected_operations: 0,
              },
            ],
            total: 2,
          }),
        });
      }
      if (url.match(/^\/api\/reviews\/\d+$/)) {
        const id = Number(url.split("/").pop());
        return Promise.resolve({
          ok: true,
          json: async () => ({
            id,
            current_revision: {
              id: id * 10,
              operations: [{ id: id * 100, decision: "pending" }],
            },
          }),
        });
      }
      if (url === "/api/reviews/7/operations" && init?.method === "PATCH") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ state: "discarded" }),
        });
      }
      if (url === "/api/reviews/8/operations" && init?.method === "PATCH") {
        return Promise.resolve({
          ok: false,
          status: 409,
          json: async () => ({ detail: "conflict" }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ReviewInbox />, { wrapper: bulkWrapper });
    await screen.findByText("a.flac");
    fireEvent.click(screen.getByLabelText("Seleziona visibili"));
    fireEvent.click(
      await screen.findByRole("button", { name: "Rifiuta selezionate (2)" }),
    );
    await waitFor(() =>
      expect(screen.getByText(/1 falliti/)).toBeInTheDocument(),
    );
    // failed selection remains: 8 should still be selected, so count 1
    expect(screen.getByText("1 selezionate")).toBeInTheDocument();
    // list still has both? but success one would be removed on refetch, but our mock still returns both, so we check selection persistence
  });

  it("exposes confidence, issue, source and session filters with URL state and chips", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/imports"))
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [
              {
                id: 1,
                library_root: "/music",
                state: "reviewing",
                job_id: 1,
                stats: {},
                error: null,
                created_at: "",
                updated_at: "",
                tasks: [],
                review_bundle_ids: [],
              },
            ],
            total: 1,
          }),
        });
      return Promise.resolve({
        ok: true,
        json: async () => ({ items: [], total: 0 }),
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    function filterWrapper({ children }: { children: ReactNode }) {
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      return (
        <QueryClientProvider client={client}>
          <ToastProvider>
            <MemoryRouter initialEntries={["/reviews"]}>
              {children}
            </MemoryRouter>
          </ToastProvider>
        </QueryClientProvider>
      );
    }
    render(<ReviewInbox />, { wrapper: filterWrapper });
    await waitFor(() =>
      expect(screen.getByLabelText("Confidenza")).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Problema")).toBeInTheDocument();
    expect(screen.getByLabelText("Provider")).toBeInTheDocument();
    expect(screen.getByLabelText("Sessione")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Confidenza"), {
      target: { value: "high_confidence" },
    });
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("confidence=high_confidence"),
        expect.any(Object),
      ),
    );
    // chips removable
    expect(screen.getByText("confidence: high_confidence")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Rimuovi filtro confidence"));
    await waitFor(() =>
      expect(
        screen.queryByText("confidence: high_confidence"),
      ).not.toBeInTheDocument(),
    );
  });
});
