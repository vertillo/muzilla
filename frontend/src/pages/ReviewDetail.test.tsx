import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, createMemoryRouter, createRoutesFromElements, RouterProvider, Route } from "react-router-dom";
import type { ReactNode } from "react";
import { ReviewDetail } from "@/pages/ReviewDetail";
import { ToastProvider } from "@/hooks/useToasts";

const review = {
  id: 7,
  logical_key: "track:7",
  title: "Review 01-source.flac",
  scope_type: "track",
  scope_id: 7,
  state: "ready",
  error: null,
  source_items: [
    {
      source_id: 7,
      filename: "01-source.flac",
      path: "/music/incoming/01-source.flac",
      format: "flac",
    },
  ],
  cover_candidates: [
    {
      id: 41,
      blob_id: 77,
      provider: "upload",
      mime: "image/jpeg",
      size: 123,
      width: 300,
      height: 300,
      thumbnail_url: "/api/reviews/7/cover/candidates/41/thumbnail",
    },
  ],
  task_attempts: [],
  apply_runs: [],
  undo_runs: [],
  current_revision: {
    id: 3,
    revision_no: 1,
    content_digest: "digest",
    candidate_source: "musicbrainz",
    candidate_ref: "release-7",
    candidate_snapshot: {
      artist: "Artist",
      title: "Track",
      album: "Album",
      year: 2026,
      duration_ms: 123000,
      position: 1,
      track_count: 10,
      signals: ["title exact"],
      penalties: [],
    },
    match_explanation: { rejection_reasons: [] },
    confidence: 0.92,
    created_at: "2026-08-01T00:00:00Z",
    operations: [
      {
        id: 11,
        seq: 1,
        kind: "set_tag",
        field: "title",
        target_type: "track",
        target_id: 7,
        current_value: "Old title",
        proposed_value: "New title",
        decision: "pending",
        provenance: {},
        validation: {},
      },
      {
        id: 12,
        seq: 2,
        kind: "move_file",
        field: "path",
        target_type: "track",
        target_id: 7,
        current_value: "/music/incoming/01-source.flac",
        proposed_value: "/music/New title.flac",
        decision: "accepted",
        provenance: {},
        validation: {},
      },
    ],
  },
};

const page = {
  items: [
    {
      id: 7,
      title: review.title,
      state: "ready",
      filename: "01-source.flac",
      path: "/music/incoming/01-source.flac",
      format: "flac",
      candidate_source: "musicbrainz",
      confidence: null,
      confidence_label: "Not scored",
      cover_thumbnail_url: null,
      issues: [],
      accepted_operations: 1,
      pending_operations: 1,
      rejected_operations: 0,
    },
  ],
  total: 1,
};

const postCoverReview = {
  ...review,
  current_revision: {
    ...review.current_revision,
    id: 4,
    revision_no: 2,
    candidate_snapshot: {
      ...review.current_revision.candidate_snapshot,
      artist: "Post-action artist",
      title: "Post-action track",
      signals: ["cover preserved candidate"],
    },
    match_explanation: { rejection_reasons: ["post-action explanation"] },
    confidence: 0.81,
  },
};

function mockFetch({
  coverResponse = review,
  reviewResponse = review,
}: {
  coverResponse?: typeof postCoverReview;
  reviewResponse?: unknown;
} = {}) {
  let coverApplied = false;
  const fetch = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/reviews/7/cover") coverApplied = true;
    const body = url.includes("/neighbors")
      ? { previous_id: 6, next_id: 8, next_unreviewed_id: 8 }
      : url === "/api/reviews/7/cover"
        ? coverResponse
        : /^\/api\/reviews\/\d+$/.test(url)
          ? coverApplied
            ? coverResponse
            : reviewResponse
          : page;
    return Promise.resolve({ ok: true, json: async () => body });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <RouterProvider
          router={createMemoryRouter(
            createRoutesFromElements(
              <Route path="/reviews/:id" element={children} />,
            ),
            { initialEntries: ["/reviews/7?returnTo=%2Freviews"] },
          )}
        />
      </ToastProvider>
    </QueryClientProvider>
  );
}

describe("ReviewDetail", () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each([
    {
      name: "manual candidate without confidence",
      response: {
        ...review,
        current_revision: {
          ...review.current_revision,
          candidate_source: "manual",
          candidate_snapshot: {
            ...review.current_revision.candidate_snapshot,
            confidence_band: "manual",
          },
          confidence: null,
        },
      },
      label: "Manual selection",
    },
    { name: "strong confidence", response: review, label: "Strong" },
    {
      name: "ambiguous confidence",
      response: {
        ...review,
        current_revision: { ...review.current_revision, confidence: 0.72 },
      },
      label: "Ambiguous",
    },
    {
      name: "low confidence",
      response: {
        ...review,
        current_revision: { ...review.current_revision, confidence: 0.4 },
      },
      label: "Low",
    },
    {
      name: "skipped candidate",
      response: {
        ...review,
        state: "discarded",
        error: "Skipped by user",
        current_revision: {
          ...review.current_revision,
          candidate_snapshot: {
            ...review.current_revision.candidate_snapshot,
            confidence_band: "skipped",
          },
          confidence: null,
        },
      },
      label: "Skipped — Leave unchanged",
    },
    {
      name: "no candidate",
      response: {
        ...review,
        state: "needs_attention",
        current_revision: {
          ...review.current_revision,
          candidate_snapshot: null,
          confidence: null,
        },
      },
      label: "Needs attention",
    },
  ])("shows the expected confidence label for $name", async ({ response, label }) => {
    mockFetch({ reviewResponse: response });
    render(<ReviewDetail />, { wrapper });

    expect(await screen.findByText(label, { exact: true })).toBeInTheDocument();
    if (label === "Manual selection")
      expect(screen.queryByText("Strong", { exact: true })).not.toBeInTheDocument();
  });

  it("keeps source identity visible and moves roving focus with J/K without acting through a dialog", async () => {
    mockFetch();
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      value: scrollIntoView,
      configurable: true,
    });
    render(<ReviewDetail />, { wrapper });

    await waitFor(() =>
      expect(screen.getByText("01-source.flac")).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Successiva" })).toBeEnabled(),
    );
    expect(
      screen.getAllByText("/music/incoming/01-source.flac")[0],
    ).toBeInTheDocument();
    expect(screen.getAllByText("Accetta")).toHaveLength(2);
    expect(screen.getAllByText("Rifiuta")).toHaveLength(2);

    fireEvent.keyDown(screen.getByText("Old title"), { key: "j" });
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "Scorciatoie" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.keyDown(dialog, { key: "j" });
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it("autosaves one decision from a multi-operation review without sending its siblings", async () => {
    const fetch = mockFetch();
    render(<ReviewDetail />, { wrapper });

    await screen.findByText("Old title");
    fireEvent.click(screen.getAllByRole("button", { name: "Accetta" })[0]);

    await waitFor(() => {
      const patch = fetch.mock.calls.find(
        ([url, init]) =>
          String(url) === "/api/reviews/7/operations" &&
          init?.method === "PATCH",
      );
      expect(patch).toBeDefined();
      if (!patch) throw new Error("Expected an operation-decision request");
      const init = patch[1];
      if (!init || typeof init.body !== "string")
        throw new Error("Expected a JSON request body");
      expect(JSON.parse(init.body)).toEqual({
        revision_id: 3,
        decisions: [{ operation_id: 11, decision: "accepted" }],
      });
    });
  });

  it("renders the candidate snapshot and sends typed cover and tag-edit mutations", async () => {
    const fetch = mockFetch();
    render(<ReviewDetail />, { wrapper });

    await screen.findByText("Artist — Track");
    expect(screen.getByText(/Confidenza 92%/)).toBeInTheDocument();
    expect(screen.getByText(/Perché: title exact/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Usa" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Modifica" })[0]);
    fireEvent.change(await screen.findByLabelText("Valore tag"), {
      target: { value: "Edited title" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Salva modifica" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/reviews/7/cover",
        expect.objectContaining({ method: "POST" }),
      );
      const edit = fetch.mock.calls.find(
        ([url, init]) =>
          String(url) === "/api/reviews/7/operations/11/edit" &&
          init?.method === "POST",
      );
      expect(edit).toBeDefined();
      if (!edit) throw new Error("Expected typed edit request");
      const init = edit[1];
      if (!init || typeof init.body !== "string")
        throw new Error("Expected a JSON request body");
      expect(JSON.parse(init.body)).toEqual({
        revision_id: 3,
        kind: "set_tag",
        value: "Edited title",
      });
    });
  });

  it("confirms a persistent per-file undo and sends the source apply run id", async () => {
    const appliedReview = {
      ...review,
      state: "applied",
      apply_runs: [
        {
          id: 51,
          revision_id: 3,
          state: "applied",
          result: {
            state: "applied",
            atomicity: "per_file",
            files: [
              {
                track_id: 7,
                state: "applied",
                applied_operation_ids: [12],
                error: null,
              },
            ],
          },
          error: null,
          operation_attempts: [],
        },
      ],
      undo_runs: [],
    };
    const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      void init;
      const url = String(input);
      const body = url.endsWith("/neighbors")
        ? { previous_id: null, next_id: null, next_unreviewed_id: null }
        : url === "/api/reviews/7/undo"
          ? { undo_run_id: 61, job_id: 71 }
          : url === "/api/jobs/71"
            ? {
                id: 71,
                type: "undo_review_bundle",
                state: "succeeded",
                progress_current: 1,
                progress_total: 1,
                progress_message: null,
                cancel_requested: false,
                error: null,
                created_at: "2026-08-01T00:00:00Z",
                started_at: null,
                finished_at: null,
                payload: {},
                result: {},
              }
            : /^\/api\/reviews\/\d+$/.test(url)
              ? appliedReview
              : page;
      return Promise.resolve({ ok: true, json: async () => body });
    });
    vi.stubGlobal("fetch", fetch);
    render(<ReviewDetail />, { wrapper });

    fireEvent.click(
      await screen.findByRole("button", { name: "Ripristina applicazione" }),
    );
    expect(
      screen.getByText(/collisioni, modifiche esterne o recovery incerta/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Ripristina file" }));

    await waitFor(() => {
      const request = fetch.mock.calls.find(
        ([url, requestInit]) =>
          String(url) === "/api/reviews/7/undo" &&
          requestInit?.method === "POST",
      );
      expect(request).toBeDefined();
      const body = request?.[1]?.body;
      expect(typeof body).toBe("string");
      expect(JSON.parse(body as string)).toEqual({ apply_run_id: 51 });
      expect(request?.[1]?.headers).toHaveProperty("Idempotency-Key");
    });
  });

  it("keeps the CandidateCard visible after a cover action returns a successor revision", async () => {
    mockFetch({ coverResponse: postCoverReview });
    render(<ReviewDetail />, { wrapper });

    await screen.findByText("Artist — Track");
    fireEvent.click(screen.getByRole("button", { name: "Usa" }));

    await screen.findByText("Post-action artist — Post-action track");
    expect(screen.getByText(/Confidenza 81%/)).toBeInTheDocument();
    expect(
      screen.getByText(/Perché: cover preserved candidate/),
    ).toBeInTheDocument();
  });

  it("uses neighbors for a directly opened review beyond the first inbox page", async () => {
    const fetch = mockFetch();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <ToastProvider>
          <RouterProvider router={createMemoryRouter(createRoutesFromElements(<>
              <Route path="/reviews/:id" element={<ReviewDetail />} />
            </>), { initialEntries: ["/reviews/101?returnTo=%2Freviews"] })} />
        </ToastProvider>
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Successiva" })).toBeEnabled(),
    );
    expect(fetch).toHaveBeenCalledWith(
      "/api/reviews/101/neighbors",
      expect.any(Object),
    );
  });

  it("renders collision details, disables Apply, and exposes recalculate control", async () => {
    const collisionReview = {
      ...review,
      state: "needs_attention",
      error: null,
      current_revision: {
        ...review.current_revision,
        id: 9,
        operations: [
          {
            id: 21,
            seq: 1,
            kind: "set_tag",
            field: "title",
            target_type: "track",
            target_id: 7,
            current_value: "Old",
            proposed_value: "Colliding Title",
            decision: "accepted",
            provenance: { section: "metadata" },
            validation: {},
          },
          {
            id: 22,
            seq: 2,
            kind: "move_file",
            field: "path",
            target_type: "track",
            target_id: 7,
            current_value: "/music/incoming/01-source.flac",
            proposed_value: "/music/Colliding Title.flac",
            decision: "accepted",
            provenance: { section: "path" },
            validation: {
              errors: [],
              collision: true,
              conflicting_track_ids: [9, 12],
              conflicting_paths: ["/music/other.flac", "/music/another.flac"],
              collision_path: "Colliding Title.flac",
            },
          },
        ],
      },
    };
    const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/reviews/7/refresh") {
        return Promise.resolve({ ok: true, json: async () => collisionReview });
      }
      const body = url.endsWith("/neighbors")
        ? { previous_id: 6, next_id: 8, next_unreviewed_id: 8 }
        : /^\/api\/reviews\/\d+$/.test(url)
          ? collisionReview
          : page;
      void init;
      return Promise.resolve({ ok: true, json: async () => body });
    });
    vi.stubGlobal("fetch", fetch);
    render(<ReviewDetail />, { wrapper });

    await waitFor(() =>
      expect(screen.getByText("01-source.flac")).toBeInTheDocument(),
    );
    const conflicts = await screen.findAllByText(/Conflitto di destinazione/);
    expect(conflicts.length).toBeGreaterThanOrEqual(2);
    // global recap lists every conflict: path + ids + conflicting paths
    expect(
      screen.getAllByText(/Colliding Title\.flac/).length,
    ).toBeGreaterThanOrEqual(1);
    expect(
      screen.getAllByText(/conflitti con track 9, 12/).length,
    ).toBeGreaterThanOrEqual(1);
    // per-operation alert also renders collision details (at least one alert per collision)
    expect(screen.getAllByRole("alert").length).toBeGreaterThanOrEqual(2);
    // Apply is blocked while collision exists (whole bundle blocked)
    const applyBtn = screen.getByRole("button", { name: /Applica/ });
    expect(applyBtn).toBeDisabled();
    // recalculate control is visible and wired to refresh endpoint (ui-ux-pro-max: explicit recovery action)
    const recalcButtons = screen.getAllByRole("button", {
      name: /Ricalcola preview/,
    });
    expect(recalcButtons.length).toBeGreaterThanOrEqual(1);
    fireEvent.click(recalcButtons[0]);
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/api/reviews/7/refresh",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("keeps detail anchored when autosave makes it leave the active filter", async () => {
    const fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/neighbors")) {
        return Promise.resolve({
          ok: false,
          status: 404,
          json: async () => ({ detail: "review is not in the selected inbox" }),
        });
      }
      if (/^\/api\/reviews\/\d+$/.test(url))
        return Promise.resolve({ ok: true, json: async () => review });
      return Promise.resolve({ ok: true, json: async () => page });
    });
    vi.stubGlobal("fetch", fetch);
    render(<ReviewDetail />, { wrapper });
    await screen.findByText("01-source.flac");
    // anchored banner should appear
    expect(
      await screen.findByText(/non è più nel filtro attivo — rimane ancorata/),
    ).toBeInTheDocument();
    // detail still shows candidate snapshot, not error page
    expect(screen.getByText("Artist — Track")).toBeInTheDocument();
    // navigation buttons disabled because neighbors error
    expect(screen.getByRole("button", { name: "Precedente" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Successiva" })).toBeDisabled();
  });

  it("does not add browser history entries when dirty or saved, and one Back/Forward renders the exact destinations", async () => {
    mockFetch();
    const inbox = "/reviews?state=ready#position";
    const detail = "/reviews/7?returnTo=" + encodeURIComponent(inbox);
    window.history.replaceState(null, "", inbox);
    const router = createBrowserRouter([
      { path: "/reviews", element: <div>Browser inbox destination</div> },
      { path: "/reviews/:id", element: <ReviewDetail /> },
    ]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(<QueryClientProvider client={client}><ToastProvider><RouterProvider router={router} /></ToastProvider></QueryClientProvider>);
    try {
      await act(async () => { await router.navigate(detail); });
      await screen.findByText("Old title");
      const length = window.history.length;
      for (let cycle = 0; cycle < 2; cycle++) {
        fireEvent.click(screen.getAllByRole("button", { name: "Modifica" })[0]);
        fireEvent.change(await screen.findByLabelText("Valore tag"), { target: { value: `Browser saved ${cycle}` } });
        expect(window.history.length).toBe(length);
        fireEvent.click(screen.getByRole("button", { name: "Salva modifica" }));
        await waitFor(() => expect(screen.queryByLabelText("Valore tag")).not.toBeInTheDocument());
        expect(window.history.length).toBe(length);
        await act(async () => { window.history.back(); });
        await screen.findByText("Browser inbox destination");
        expect(window.location.pathname + window.location.search + window.location.hash).toBe(inbox);
        await act(async () => { window.history.forward(); });
        await screen.findByText("Old title");
        expect(window.location.pathname + window.location.search).toBe(detail);
      }
    } finally {
      view.unmount();
      router.dispose();
      window.history.replaceState(null, "", "/");
    }
  });

  it.each([-1, 1])("blocks real history POP repeatedly and proceeds in direction %s to the exact URL and state once", async (discardDirection) => {
    const fetch = mockFetch();
    const inbox = "/reviews?state=ready&source=manual#position";
    const detail = "/reviews/7?returnTo=" + encodeURIComponent(inbox);
    const router = createMemoryRouter([
      { path: "/reviews", element: <div>Filtered inbox destination</div> },
      { path: "/reviews/:id", element: <ReviewDetail /> },
      { path: "/catalog", element: <div>Forward catalog destination</div> },
    ], { initialEntries: [{ pathname: "/reviews", search: "?state=ready&source=manual", hash: "#position", state: { position: 42 } }, detail, "/catalog?q=forward#track"], initialIndex: 1 });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><ToastProvider><RouterProvider router={router} /></ToastProvider></QueryClientProvider>);
    await screen.findByText("Old title");
    fireEvent.click(screen.getAllByRole("button", { name: "Modifica" })[0]);
    fireEvent.change(await screen.findByLabelText("Valore tag"), { target: { value: "Dirty POP" } });
    for (const direction of [-1, 1, -1, 1]) {
      await act(async () => { await router.navigate(direction); });
      await screen.findByRole("dialog", { name: "Modifiche non salvate" });
      fireEvent.click(screen.getByRole("button", { name: "Restare" }));
      expect(router.state.location.pathname + router.state.location.search).toBe(detail);
      expect(screen.getByDisplayValue("Dirty POP")).toBeInTheDocument();
    }
    await act(async () => { await router.navigate(discardDirection); });
    fireEvent.click(await screen.findByRole("button", { name: "Scartare" }));
    await screen.findByText(discardDirection === 1 ? "Forward catalog destination" : "Filtered inbox destination");
    expect(router.state.location.pathname + router.state.location.search + router.state.location.hash).toBe(discardDirection === 1 ? "/catalog?q=forward#track" : inbox);
    await act(async () => { await router.navigate(-discardDirection); });
    await screen.findByText("Old title");
    await act(async () => { await router.navigate(-1); });
    await screen.findByText("Filtered inbox destination");
    expect(router.state.location.pathname + router.state.location.search + router.state.location.hash).toBe(inbox);
    expect(router.state.location.state).toEqual({ position: 42 });
    expect(fetch.mock.calls.filter(([u]) => String(u).includes("/edit"))).toHaveLength(0);
  });

  it.each([false, true])("keeps failed saves dirty and cancels a blocked departure on save success (pending departure: %s)", async (pendingDeparture) => {
    const fetch = mockFetch();
    const normalFetch = fetch.getMockImplementation()!;
    let finishSave!: (response: Awaited<ReturnType<typeof normalFetch>>) => void;
    let failSave!: (error: Error) => void;
    fetch.mockImplementation((input, init) => String(input).includes("/edit")
      ? new Promise((resolve, reject) => { finishSave = resolve; failSave = reject; }) as ReturnType<typeof normalFetch>
      : normalFetch(input, init));
    const inbox = "/reviews?state=ready#position";
    const detail = "/reviews/7?returnTo=" + encodeURIComponent(inbox);
    const router = createMemoryRouter([
      { path: "/reviews", element: <div>Saved inbox destination</div> },
      { path: "/reviews/:id", element: <ReviewDetail /> },
    ], { initialEntries: [inbox, detail], initialIndex: 1 });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><ToastProvider><RouterProvider router={router} /></ToastProvider></QueryClientProvider>);
    await screen.findByText("Old title");
    for (let cycle = 0; cycle < 2; cycle++) {
      fireEvent.click(screen.getAllByRole("button", { name: "Modifica" })[0]);
      fireEvent.change(await screen.findByLabelText("Valore tag"), { target: { value: `Saved cycle ${cycle}` } });
      const unload = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(unload);
      expect(unload.defaultPrevented).toBe(true);
      fireEvent.click(screen.getByRole("button", { name: "Salva modifica" }));
      await waitFor(() => expect(fetch.mock.calls.filter(([u]) => String(u).includes("/edit"))).toHaveLength(cycle * 2 + 1));
      await act(async () => { failSave(new Error("Save request failed")); });
      await screen.findByText(/La modifica non è valida/);
      expect(screen.getByDisplayValue(`Saved cycle ${cycle}`)).toBeInTheDocument();
      await act(async () => { await router.navigate(-1); });
      await screen.findByRole("dialog", { name: "Modifiche non salvate" });
      fireEvent.click(screen.getByRole("button", { name: "Restare" }));
      fireEvent.click(screen.getByRole("button", { name: "Salva modifica" }));
      await waitFor(() => expect(fetch.mock.calls.filter(([u]) => String(u).includes("/edit"))).toHaveLength(cycle * 2 + 2));
      if (pendingDeparture) {
        await act(async () => { await router.navigate(-1); });
        await screen.findByRole("dialog", { name: "Modifiche non salvate" });
      }
      await act(async () => { finishSave({ ok: true, json: async () => review }); });
      await waitFor(() => expect(screen.queryByLabelText("Valore tag")).not.toBeInTheDocument());
      expect(screen.queryByRole("dialog", { name: "Modifiche non salvate" })).not.toBeInTheDocument();
      expect(router.state.location.pathname + router.state.location.search).toBe(detail);
      const cleanUnload = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(cleanUnload);
      expect(cleanUnload.defaultPrevented).toBe(false);
      await act(async () => { await router.navigate(-1); });
      await screen.findByText("Saved inbox destination");
      expect(router.state.location.pathname + router.state.location.search + router.state.location.hash).toBe(inbox);
      await act(async () => { await router.navigate(1); });
      await screen.findByText("Old title");
      expect(router.state.location.pathname + router.state.location.search).toBe(detail);
    }
  });

  it.each([
    { returnTo: "/\\attacker.example/", expected: "/reviews" },
    { returnTo: "//attacker.example/", expected: "/reviews" },
    { returnTo: "/reviews/..//attacker.example/", expected: "/reviews" },
    { returnTo: "\\/attacker.example/", expected: "/reviews" },
    { returnTo: "/\\\\attacker.example/", expected: "/reviews" },
    { returnTo: "/\t/attacker.example/", expected: "/reviews" },
    { returnTo: "/\n/attacker.example/", expected: "/reviews" },
    { returnTo: "/\r/attacker.example/", expected: "/reviews" },
    { returnTo: "/reviews\u0000?state=ready", expected: "/reviews" },
    { returnTo: "/reviews\u007f", expected: "/reviews" },
    { returnTo: "https://attacker.example/reviews", expected: "/reviews" },
    { returnTo: "reviews?state=ready", expected: "/reviews" },
    { returnTo: "/reviews?state=ready&source=manual#review-7", expected: "/reviews?state=ready&source=manual#review-7" },
    { returnTo: "/catalog/../reviews?state=ready#review-7", expected: "/reviews?state=ready#review-7" },
  ])("validates returnTo=$returnTo for breadcrumb and Close without external navigation", async ({ returnTo, expected }) => {
    mockFetch();
    for (const action of ["Revisioni", "Chiudi"]) {
      const detail = "/reviews/7?returnTo=" + encodeURIComponent(returnTo);
      const router = createMemoryRouter([
        { path: "/reviews/:id", element: <ReviewDetail /> },
        { path: "/reviews", element: <div>Validated internal inbox destination</div> },
      ], { initialEntries: [detail] });
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const view = render(<QueryClientProvider client={client}><ToastProvider><RouterProvider router={router} /></ToastProvider></QueryClientProvider>);
      try {
        await screen.findByText("Old title");
        expect(screen.getByRole("link", { name: "Revisioni" })).toHaveAttribute("href", expected);
        fireEvent.click(screen.getAllByRole("button", { name: "Modifica" })[0]);
        fireEvent.change(await screen.findByLabelText("Valore tag"), { target: { value: "Dirty validated return" } });
        fireEvent.click(screen.getByRole(action === "Revisioni" ? "link" : "button", { name: action }));
        await screen.findByRole("dialog", { name: "Modifiche non salvate" });
        fireEvent.click(screen.getByRole("button", { name: "Scartare" }));
        await screen.findByText("Validated internal inbox destination");
        expect(router.state.location.pathname + router.state.location.search + router.state.location.hash).toBe(expected);
        expect(new URL(expected, window.location.origin).origin).toBe(window.location.origin);
      } finally {
        view.unmount();
        router.dispose();
        client.clear();
      }
    }
  });

  it.each(["Chiudi", "Revisioni", "Precedente", "Successiva", "query"])('preserves the exact blocked %s transition and replace intent', async (action) => {
    mockFetch();
    const inbox = "/reviews?state=ready&source=manual#position";
    const detail = "/reviews/7?returnTo=" + encodeURIComponent(inbox);
    const router = createMemoryRouter([
      { path: "/reviews", element: <div>Origin inbox destination</div> },
      { path: "/reviews/:id", element: <ReviewDetail /> },
    ], { initialEntries: [inbox, detail], initialIndex: 1 });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><ToastProvider><RouterProvider router={router} /></ToastProvider></QueryClientProvider>);
    await screen.findByText("Old title");
    fireEvent.click(screen.getAllByRole("button", { name: "Modifica" })[0]);
    fireEvent.change(await screen.findByLabelText("Valore tag"), { target: { value: "Unsaved transition" } });
    const queryTarget = detail + "&filter=pending#metadata";
    const attempt = async () => {
      if (action === "query") await act(async () => { await router.navigate(queryTarget, { state: { filter: "pending" } }); });
      else fireEvent.click(screen.getByRole(action === "Revisioni" ? "link" : "button", { name: action }));
      await screen.findByRole("dialog", { name: "Modifiche non salvate" });
    };
    await attempt();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(router.state.location.pathname + router.state.location.search).toBe(detail);
    expect(screen.getByDisplayValue("Unsaved transition")).toBeInTheDocument();
    await attempt();
    fireEvent.click(screen.getByRole("button", { name: "Scartare" }));
    const expected = action === "query" ? queryTarget : action === "Precedente" ? detail.replace("/7?", "/6?") : action === "Successiva" ? detail.replace("/7?", "/8?") : inbox;
    await waitFor(() => expect(router.state.location.pathname + router.state.location.search + router.state.location.hash).toBe(expected));
    if (action === "query") expect(router.state.location.state).toEqual({ filter: "pending" });
    if (["Chiudi", "Revisioni"].includes(action)) await screen.findByText("Origin inbox destination");
    else await screen.findByText("Old title");
    if (["Precedente", "Successiva"].includes(action)) {
      await act(async () => { await router.navigate(-1); });
      await screen.findByText("Origin inbox destination");
      expect(router.state.location.pathname + router.state.location.search + router.state.location.hash).toBe(inbox);
    }
  });

  it("blocks navigation while typed edits are unsaved and requires Restare or Scartare", async () => {
    const fetchMock = mockFetch();
    render(<ReviewDetail />, { wrapper });
    await screen.findByText("Old title");
    fireEvent.click(screen.getAllByRole("button", { name: "Modifica" })[0]);
    const input = await screen.findByLabelText("Valore tag");
    fireEvent.change(input, { target: { value: "Dirty value" } });
    // attempt to close should show discard modal
    fireEvent.click(screen.getByRole("button", { name: "Chiudi" }));
    expect(
      await screen.findByRole("dialog", { name: "Modifiche non salvate" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Hai modifiche non salvate/)).toBeInTheDocument();
    // Restare stays
    fireEvent.click(screen.getByRole("button", { name: "Restare" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Modifiche non salvate" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByDisplayValue("Dirty value")).toBeInTheDocument();
    // attempt again and Scartare discards
    fireEvent.click(screen.getByRole("button", { name: "Chiudi" }));
    expect(
      await screen.findByRole("dialog", { name: "Modifiche non salvate" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Scartare" }));
    await waitFor(() =>
      expect(screen.queryByDisplayValue("Dirty value")).not.toBeInTheDocument(),
    );
    // edits never implicitly saved: no edit request should have been sent
    const editCalls = fetchMock.mock.calls.filter(([u]) =>
      String(u).includes("/edit"),
    );
    expect(editCalls.length).toBe(0);
  });

  it("requires Restare/Scartare when closing dirty editor via Annulla or Escape", async () => {
    const fetchMock = mockFetch();
    render(<ReviewDetail />, { wrapper });
    await screen.findByText("Old title");
    fireEvent.click(screen.getAllByRole("button", { name: "Modifica" })[0]);
    const input = await screen.findByLabelText("Valore tag");
    fireEvent.change(input, { target: { value: "Dirty editor" } });
    // Annulla (editor footer) must not implicitly close when dirty
    fireEvent.click(screen.getByRole("button", { name: "Annulla" }));
    expect(
      await screen.findByRole("dialog", { name: "Modifiche non salvate" }),
    ).toBeInTheDocument();
    // Restare keeps editor open with dirty value
    fireEvent.click(screen.getByRole("button", { name: "Restare" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Modifiche non salvate" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByDisplayValue("Dirty editor")).toBeInTheDocument();
    // Escape on editor must also require explicit choice
    fireEvent.keyDown(document, { key: "Escape" });
    expect(
      await screen.findByRole("dialog", { name: "Modifiche non salvate" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Scartare" }));
    await waitFor(() =>
      expect(
        screen.queryByDisplayValue("Dirty editor"),
      ).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("dialog", { name: "Modifica title" }),
    ).not.toBeInTheDocument();
    const editCalls = fetchMock.mock.calls.filter(([u]) =>
      String(u).includes("/edit"),
    );
    expect(editCalls.length).toBe(0);
  });

  it("exposes confidence/issue/source/session filters without losing URL state", async () => {
    const fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/neighbors"))
        return Promise.resolve({
          ok: true,
          json: async () => ({
            previous_id: null,
            next_id: null,
            next_unreviewed_id: null,
          }),
        });
      if (/^\/api\/reviews\/\d+$/.test(url))
        return Promise.resolve({ ok: true, json: async () => review });
      return Promise.resolve({ ok: true, json: async () => page });
    });
    vi.stubGlobal("fetch", fetch);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <ToastProvider>
          <RouterProvider router={createMemoryRouter([
            { path: "/reviews/:id", element: <ReviewDetail /> },
          ], { initialEntries: ["/reviews/7?returnTo=%2Freviews%3Fconfidence%3Dhigh_confidence%26issue%3Dreview%26source%3Dmusicbrainz%26session%3D1"] })} />
        </ToastProvider>
      </QueryClientProvider>,
    );
    await screen.findByText("01-source.flac");
    const neighborCall = fetch.mock.calls.find(([u]) =>
      String(u).includes("/neighbors"),
    );
    expect(neighborCall).toBeDefined();
    const neighborUrl = String(neighborCall![0]);
    expect(neighborUrl).toContain("confidence=high_confidence");
    expect(neighborUrl).toContain("issue=review");
    expect(neighborUrl).toContain("source=musicbrainz");
    expect(neighborUrl).toContain("session=1");
    // URL state preserved in returnTo
    expect(
      screen.getByRole("link", { name: "Revisioni" }).getAttribute("href"),
    ).toBe(
      "/reviews?confidence=high_confidence&issue=review&source=musicbrainz&session=1",
    );
  });

  it("disables Undo and explains expiry when retention has pruned journals (age/count threshold)", async () => {
    const expiredReview = {
      ...review,
      state: "applied",
      apply_runs: [
        {
          id: 51,
          revision_id: 3,
          state: "applied",
          result: { state: "applied", atomicity: "review_bundle", files: [], recovery_required: false },
          error: null,
          operation_attempts: [{ operation_id: 11, attempted_value: "New title", state: "applied", error: null }],
          undo_expired: true,
          undo_expiry_reason: "expired — journal retention window elapsed (age or count threshold)",
        },
      ],
      undo_runs: [],
    };
    const fetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/neighbors"))
        return Promise.resolve({ ok: true, json: async () => ({ previous_id: null, next_id: null, next_unreviewed_id: null }) });
      if (/^\/api\/reviews\/\d+$/.test(url)) return Promise.resolve({ ok: true, json: async () => expiredReview });
      return Promise.resolve({ ok: true, json: async () => page });
    });
    vi.stubGlobal("fetch", fetch);
    render(<ReviewDetail />, { wrapper });
    await screen.findByText("01-source.flac");
    // expiry explanation must be visible and actionable Undo disabled (multiple matches: paragraph + button)
    const scadutoMatches = await screen.findAllByText(/Ripristino scaduto/);
    expect(scadutoMatches.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/journal retention window elapsed/)).toBeInTheDocument();
    const disabledBtn = screen.getByRole("button", { name: "Ripristino scaduto" });
    expect(disabledBtn).toBeDisabled();
    // ensure no active "Ripristina applicazione" button is enabled
    const activeUndo = screen.queryByRole("button", { name: "Ripristina applicazione" });
    expect(activeUndo).not.toBeInTheDocument();
  });
});
