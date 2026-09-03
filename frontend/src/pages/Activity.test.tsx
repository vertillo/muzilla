import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/hooks/useToasts";
import { Jobs } from "@/pages/Jobs";

function createWrapper(
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
    return function wrapper({ children }: { children: ReactNode }) {
        return (
            <QueryClientProvider client={client}>
                <MemoryRouter>
                    <ToastProvider>{children}</ToastProvider>
                </MemoryRouter>
            </QueryClientProvider>
        );
    };
}

class MockEventSource {
    url: string;
    onmessage: ((msg: any) => void) | null = null;
    constructor(url: string) {
        this.url = url;
    }
    addEventListener() {}
    close() {}
}

describe("Activity grouping", () => {
    beforeEach(() => vi.stubGlobal("EventSource", MockEventSource as any));
    afterEach(() => vi.unstubAllGlobals());

    it("groups by user action with human labels and hides raw job type in primary row", async () => {
        vi.stubGlobal(
            "fetch",
            vi.fn((input: RequestInfo | URL) => {
                const url =
                    typeof input === "string" ? input : input.toString();
                if (url.includes("/api/activity")) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            items: [
                                {
                                    id: "import:1",
                                    kind: "import",
                                    title: "Importazione /music",
                                    state: "succeeded",
                                    created_at: new Date().toISOString(),
                                    updated_at: new Date().toISOString(),
                                    job_id: 10,
                                    import_session_id: 1,
                                    review_bundle_id: null,
                                    apply_run_id: null,
                                    undo_run_id: null,
                                    progress_current: 4,
                                    progress_total: 4,
                                    progress_message: "importazione completata",
                                    error: null,
                                    result: { scanned: 5, added: 2 },
                                    cancellable: false,
                                },
                                {
                                    id: "apply:2",
                                    kind: "apply",
                                    title: "Applicazione: Test Bundle",
                                    state: "succeeded",
                                    created_at: new Date().toISOString(),
                                    updated_at: new Date().toISOString(),
                                    job_id: 11,
                                    import_session_id: null,
                                    review_bundle_id: 7,
                                    apply_run_id: 2,
                                    undo_run_id: null,
                                    progress_current: 1,
                                    progress_total: 1,
                                    progress_message: null,
                                    error: null,
                                    result: {
                                        state: "applied",
                                        files: [{ track_id: 1 }],
                                    },
                                    cancellable: false,
                                },
                                {
                                    id: "job:3",
                                    kind: "duplicate_analysis",
                                    title: "Analisi duplicati",
                                    state: "running",
                                    created_at: new Date().toISOString(),
                                    updated_at: new Date().toISOString(),
                                    job_id: 12,
                                    import_session_id: null,
                                    review_bundle_id: null,
                                    apply_run_id: null,
                                    undo_run_id: null,
                                    progress_current: 1,
                                    progress_total: 2,
                                    progress_message: "scanning",
                                    error: null,
                                    result: null,
                                    cancellable: true,
                                },
                            ],
                            next_cursor: null,
                        }),
                    });
                }
                if (url.startsWith("/api/capabilities")) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            replaygain: {
                                name: "replaygain",
                                state: "available",
                                enabled: true,
                                available: true,
                                detail: "ok",
                            },
                        }),
                    });
                }
                // job detail/events fallback for diagnostics
                if (url.includes("/api/jobs/")) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            id: 10,
                            type: "import",
                            state: "succeeded",
                            payload: {},
                            result: {},
                        }),
                    });
                }
                return Promise.resolve({ ok: true, json: async () => ({}) });
            }),
        );

        render(<Jobs />, { wrapper: createWrapper() });

        expect(await screen.findByText("Importazione")).toBeInTheDocument();
        expect(screen.getByText("Applicazione")).toBeInTheDocument();
        expect(screen.getByText("Duplicati")).toBeInTheDocument();
        // primary row should show human title, not raw job type 'detect_duplicates' or 'import' alone
        expect(screen.getByText("Importazione /music")).toBeInTheDocument();
        expect(screen.getByText("Analisi duplicati")).toBeInTheDocument();
        // raw technical name should not be primary badge text
        expect(screen.queryByText("detect_duplicates")).not.toBeInTheDocument();
        expect(
            screen.queryByText("apply_review_bundle"),
        ).not.toBeInTheDocument();
    });

    it("shows outcome, progress and cancellable Annulla for running activity", async () => {
        vi.stubGlobal(
            "fetch",
            vi.fn((input: RequestInfo | URL) => {
                const url =
                    typeof input === "string" ? input : input.toString();
                if (url.includes("/api/activity")) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            items: [
                                {
                                    id: "job:5",
                                    kind: "scan",
                                    title: "Scansione cartella /music",
                                    state: "running",
                                    created_at: new Date().toISOString(),
                                    updated_at: new Date().toISOString(),
                                    job_id: 5,
                                    import_session_id: null,
                                    review_bundle_id: null,
                                    apply_run_id: null,
                                    undo_run_id: null,
                                    progress_current: 3,
                                    progress_total: 10,
                                    progress_message: "scanning",
                                    error: null,
                                    result: null,
                                    cancellable: true,
                                },
                            ],
                            next_cursor: null,
                        }),
                    });
                }
                if (url.startsWith("/api/capabilities")) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            replaygain: {
                                name: "replaygain",
                                state: "available",
                                enabled: true,
                                available: true,
                                detail: "ok",
                            },
                        }),
                    });
                }
                if (url.match(/\/api\/jobs\/5$/)) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            id: 5,
                            type: "scan",
                            state: "running",
                            payload: { root: "/music" },
                            result: null,
                            progress_current: 3,
                            progress_total: 10,
                            progress_message: "scanning",
                            error: null,
                        }),
                    });
                }
                if (url.includes("/api/jobs/5/events")) {
                    return Promise.resolve({
                        ok: false,
                        status: 404,
                        statusText: "not found",
                        json: async () => ({}),
                    } as Response);
                }
                return Promise.resolve({ ok: true, json: async () => ({}) });
            }),
        );

        render(<Jobs />, { wrapper: createWrapper() });

        expect(
            await screen.findByText("Scansione cartella /music"),
        ).toBeInTheDocument();
        expect(screen.getByText("In corso")).toBeInTheDocument();
        // progress bar should be present (percent)
        // expand to see cancel
        const row = screen
            .getByText("Scansione cartella /music")
            .closest('div[role="button"]')!;
        await userEvent.click(row);
        expect(
            await screen.findByRole("button", { name: "Annulla" }),
        ).toBeInTheDocument();
    });

    it("moves technical details behind Diagnostica tecnica", async () => {
        vi.stubGlobal(
            "fetch",
            vi.fn((input: RequestInfo | URL) => {
                const url =
                    typeof input === "string" ? input : input.toString();
                if (url.includes("/api/activity")) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            items: [
                                {
                                    id: "apply:9",
                                    kind: "apply",
                                    title: "Applicazione: Bundle 9",
                                    state: "succeeded",
                                    created_at: new Date().toISOString(),
                                    updated_at: new Date().toISOString(),
                                    job_id: 9,
                                    import_session_id: null,
                                    review_bundle_id: 9,
                                    apply_run_id: 9,
                                    undo_run_id: null,
                                    progress_current: null,
                                    progress_total: null,
                                    progress_message: null,
                                    error: null,
                                    result: { state: "applied", files: [] },
                                    cancellable: false,
                                },
                            ],
                            next_cursor: null,
                        }),
                    });
                }
                if (url.startsWith("/api/capabilities")) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            replaygain: {
                                name: "replaygain",
                                state: "available",
                                enabled: true,
                                available: true,
                                detail: "ok",
                            },
                        }),
                    });
                }
                if (url.includes("/api/jobs/9")) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            id: 9,
                            type: "apply_review_bundle",
                            state: "succeeded",
                            payload: { apply_run_id: 9 },
                            result: { state: "applied" },
                            error: null,
                        }),
                    });
                }
                return Promise.resolve({
                    ok: true,
                    json: async () => ({ events: [] }),
                });
            }),
        );

        render(<Jobs />, { wrapper: createWrapper() });

        expect(
            await screen.findByText("Applicazione: Bundle 9"),
        ).toBeInTheDocument();
        const row = screen
            .getByText("Applicazione: Bundle 9")
            .closest('div[role="button"]')!;
        await userEvent.click(row);
        // diagnostics should be collapsed by default behind details summary
        const diag = await screen.findByText("Diagnostica tecnica");
        expect(diag).toBeInTheDocument();
        // technical correlation should be inside diagnostics, not primary
        const details = diag.closest("details")!;
        // closed details should not show raw job type openly; open it
        await userEvent.click(diag);
        await waitFor(() =>
            expect(
                within(details).getByText(/Correlazione job/),
            ).toBeInTheDocument(),
        );
        // primary row should not contain raw apply_review_bundle
        expect(
            screen.queryByText("apply_review_bundle"),
        ).not.toBeInTheDocument();
    });

    it("hides system activity by default behind toggle", async () => {
        let requestedIncludeSystem = false;
        vi.stubGlobal(
            "fetch",
            vi.fn((input: RequestInfo | URL) => {
                const url =
                    typeof input === "string" ? input : input.toString();
                if (url.includes("/api/activity")) {
                    const u = new URL(url, "http://test");
                    requestedIncludeSystem =
                        u.searchParams.get("include_system") === "true";
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            items: requestedIncludeSystem
                                ? [
                                      {
                                          id: "job:99",
                                          kind: "scan",
                                          title: "Pulizia cronologia di annullamento",
                                          state: "succeeded",
                                          created_at: new Date().toISOString(),
                                          updated_at: new Date().toISOString(),
                                          job_id: 99,
                                          import_session_id: null,
                                          review_bundle_id: null,
                                          apply_run_id: null,
                                          undo_run_id: null,
                                          progress_current: null,
                                          progress_total: null,
                                          progress_message: null,
                                          error: null,
                                          result: null,
                                          cancellable: false,
                                      },
                                  ]
                                : [],
                            next_cursor: null,
                        }),
                    });
                }
                if (url.startsWith("/api/capabilities")) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            replaygain: {
                                name: "replaygain",
                                state: "available",
                                enabled: true,
                                available: true,
                                detail: "ok",
                            },
                        }),
                    });
                }
                return Promise.resolve({ ok: true, json: async () => ({}) });
            }),
        );

        render(<Jobs />, { wrapper: createWrapper() });

        expect(await screen.findByText("Nessuna attività")).toBeInTheDocument();
        expect(
            screen.queryByText("Pulizia cronologia di annullamento"),
        ).not.toBeInTheDocument();
        expect(requestedIncludeSystem).toBe(false);

        await userEvent.click(
            screen.getByRole("button", { name: "Mostra attività di sistema" }),
        );
        await waitFor(() =>
            expect(
                screen.getByText("Pulizia cronologia di annullamento"),
            ).toBeInTheDocument(),
        );
        expect(requestedIncludeSystem).toBe(true);
    });

    it("shows cancelling state visibly for import/apply/undo and hides Annulla", async () => {
        vi.stubGlobal(
            "fetch",
            vi.fn((input: RequestInfo | URL) => {
                const url =
                    typeof input === "string" ? input : input.toString();
                if (url.includes("/api/activity")) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            items: [
                                {
                                    id: "import:10",
                                    kind: "import",
                                    title: "Importazione /music/cancel",
                                    state: "cancelling",
                                    created_at: new Date().toISOString(),
                                    updated_at: new Date().toISOString(),
                                    job_id: 10,
                                    import_session_id: 10,
                                    review_bundle_id: null,
                                    apply_run_id: null,
                                    undo_run_id: null,
                                    progress_current: 2,
                                    progress_total: 4,
                                    progress_message: "stage: scan",
                                    error: null,
                                    result: null,
                                    cancellable: false,
                                },
                                {
                                    id: "apply:11",
                                    kind: "apply",
                                    title: "Applicazione: Bundle Cancel",
                                    state: "cancelling",
                                    created_at: new Date().toISOString(),
                                    updated_at: new Date().toISOString(),
                                    job_id: 11,
                                    import_session_id: null,
                                    review_bundle_id: 7,
                                    apply_run_id: 11,
                                    undo_run_id: null,
                                    progress_current: 0,
                                    progress_total: 1,
                                    progress_message: "applicazione in corso",
                                    error: null,
                                    result: null,
                                    cancellable: false,
                                },
                                {
                                    id: "undo:12",
                                    kind: "undo",
                                    title: "Annullamento: Bundle Cancel",
                                    state: "cancelling",
                                    created_at: new Date().toISOString(),
                                    updated_at: new Date().toISOString(),
                                    job_id: 12,
                                    import_session_id: null,
                                    review_bundle_id: 7,
                                    apply_run_id: 5,
                                    undo_run_id: 12,
                                    progress_current: 0,
                                    progress_total: 1,
                                    progress_message: "ripristino in corso",
                                    error: null,
                                    result: null,
                                    cancellable: false,
                                },
                            ],
                            next_cursor: null,
                        }),
                    });
                }
                if (url.startsWith("/api/capabilities")) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            replaygain: {
                                name: "replaygain",
                                state: "available",
                                enabled: true,
                                available: true,
                                detail: "ok",
                            },
                        }),
                    });
                }
                if (url.includes("/api/jobs/")) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            id: 10,
                            type: "import",
                            state: "cancelling",
                            payload: {},
                            result: null,
                        }),
                    });
                }
                return Promise.resolve({ ok: true, json: async () => ({}) });
            }),
        );

        render(<Jobs />, { wrapper: createWrapper() });

        // primary rows show cancelling badge, not running or succeeded
        expect(
            await screen.findByText("Importazione /music/cancel"),
        ).toBeInTheDocument();
        const cancellingBadges = await screen.findAllByText("Annullamento…");
        expect(cancellingBadges.length).toBeGreaterThanOrEqual(3);

        // expand first cancelling item - should show "Annullamento in corso…" and no Annulla button
        const importRow = screen
            .getByText("Importazione /music/cancel")
            .closest('div[role="button"]')!;
        await userEvent.click(importRow);
        expect(
            await screen.findByText("Annullamento in corso…"),
        ).toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: "Annulla" }),
        ).not.toBeInTheDocument();

        const applyRow = screen
            .getByText("Applicazione: Bundle Cancel")
            .closest('div[role="button"]')!;
        await userEvent.click(applyRow);
        // both detail panels share same cancelling message, still visible
        expect(
            screen.getAllByText("Annullamento in corso…").length,
        ).toBeGreaterThanOrEqual(1);
    });

    it("paginates with Carica altro using keyset cursor and loads next page", async () => {
        const firstItems = [
            {
                id: "import:2",
                kind: "import",
                title: "Importazione /music/2",
                state: "succeeded",
                created_at: new Date(Date.now() - 1000).toISOString(),
                updated_at: new Date(Date.now() - 1000).toISOString(),
                job_id: 20,
                import_session_id: 2,
                review_bundle_id: null,
                apply_run_id: null,
                undo_run_id: null,
                progress_current: 1,
                progress_total: 1,
                progress_message: null,
                error: null,
                result: null,
                cancellable: false,
            },
            {
                id: "import:1",
                kind: "import",
                title: "Importazione /music/1",
                state: "succeeded",
                created_at: new Date(Date.now() - 2000).toISOString(),
                updated_at: new Date(Date.now() - 2000).toISOString(),
                job_id: 21,
                import_session_id: 1,
                review_bundle_id: null,
                apply_run_id: null,
                undo_run_id: null,
                progress_current: 1,
                progress_total: 1,
                progress_message: null,
                error: null,
                result: null,
                cancellable: false,
            },
        ];
        const secondItems = [
            {
                id: "job:3",
                kind: "scan",
                title: "Scansione cartella /music/paged",
                state: "succeeded",
                created_at: new Date(Date.now() - 3000).toISOString(),
                updated_at: new Date(Date.now() - 3000).toISOString(),
                job_id: 22,
                import_session_id: null,
                review_bundle_id: null,
                apply_run_id: null,
                undo_run_id: null,
                progress_current: null,
                progress_total: null,
                progress_message: null,
                error: null,
                result: null,
                cancellable: false,
            },
        ];
        vi.stubGlobal(
            "fetch",
            vi.fn((input: RequestInfo | URL) => {
                const url =
                    typeof input === "string" ? input : input.toString();
                if (url.includes("/api/activity")) {
                    const u = new URL(url, "http://test");
                    const cursor = u.searchParams.get("cursor");
                    if (!cursor) {
                        return Promise.resolve({
                            ok: true,
                            json: async () => ({
                                items: firstItems,
                                next_cursor: "test-cursor-token",
                            }),
                        });
                    }
                    if (cursor === "test-cursor-token") {
                        return Promise.resolve({
                            ok: true,
                            json: async () => ({
                                items: secondItems,
                                next_cursor: null,
                            }),
                        });
                    }
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({ items: [], next_cursor: null }),
                    });
                }
                if (url.startsWith("/api/capabilities")) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            replaygain: {
                                name: "replaygain",
                                state: "available",
                                enabled: true,
                                available: true,
                                detail: "ok",
                            },
                        }),
                    });
                }
                return Promise.resolve({ ok: true, json: async () => ({}) });
            }),
        );

        render(<Jobs />, { wrapper: createWrapper() });

        expect(
            await screen.findByText("Importazione /music/2"),
        ).toBeInTheDocument();
        expect(screen.getByText("Importazione /music/1")).toBeInTheDocument();
        expect(
            screen.queryByText("Scansione cartella /music/paged"),
        ).not.toBeInTheDocument();
        const loadMore = await screen.findByRole("button", {
            name: "Carica altro",
        });
        expect(loadMore).toBeInTheDocument();

        await userEvent.click(loadMore);
        await waitFor(() =>
            expect(
                screen.getByText("Scansione cartella /music/paged"),
            ).toBeInTheDocument(),
        );
        // after loading, button should disappear because next_cursor is null
        await waitFor(() =>
            expect(
                screen.queryByRole("button", { name: "Carica altro" }),
            ).not.toBeInTheDocument(),
        );
        // first page items remain visible after pagination
        expect(screen.getByText("Importazione /music/2")).toBeInTheDocument();
    });

    it("immediately shows cancelling after Annulla click via cache projection", async () => {
        let cancelCalled = false;
        const runningItem = {
            id: "job:77",
            kind: "scan" as const,
            title: "Scansione cartella /music/cancel-me",
            state: "running" as const,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            job_id: 77,
            import_session_id: null,
            review_bundle_id: null,
            apply_run_id: null,
            undo_run_id: null,
            progress_current: 5,
            progress_total: 10,
            progress_message: "scanning",
            error: null,
            result: null,
            cancellable: true,
        };
        vi.stubGlobal(
            "fetch",
            vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
                const url =
                    typeof input === "string" ? input : input.toString();
                const method = (init?.method ?? "GET").toUpperCase();
                if (url.includes("/api/activity") && method === "GET") {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            items: [runningItem],
                            next_cursor: null,
                        }),
                    });
                }
                if (url.includes("/api/jobs/77/cancel") && method === "POST") {
                    cancelCalled = true;
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            id: 77,
                            type: "scan",
                            state: "cancelling",
                            payload: { root: "/music/cancel-me" },
                            result: null,
                            progress_current: 5,
                            progress_total: 10,
                            progress_message: "cancelling",
                            error: null,
                        }),
                    });
                }
                if (url === "/api/jobs/77" && method === "GET") {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            id: 77,
                            type: "scan",
                            state: "cancelling",
                            payload: { root: "/music/cancel-me" },
                            result: null,
                            error: null,
                        }),
                    });
                }
                if (url.includes("/api/jobs/77/events")) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({ events: [] }),
                    } as unknown as Response);
                }
                if (url.startsWith("/api/capabilities")) {
                    return Promise.resolve({
                        ok: true,
                        json: async () => ({
                            replaygain: {
                                name: "replaygain",
                                state: "available",
                                enabled: true,
                                available: true,
                                detail: "ok",
                            },
                        }),
                    });
                }
                return Promise.resolve({ ok: true, json: async () => ({}) });
            }),
        );

        render(<Jobs />, { wrapper: createWrapper() });

        expect(
            await screen.findByText("Scansione cartella /music/cancel-me"),
        ).toBeInTheDocument();
        expect(screen.getByText("In corso")).toBeInTheDocument();

        const row = screen
            .getByText("Scansione cartella /music/cancel-me")
            .closest('div[role="button"]')!;
        await userEvent.click(row);
        const cancelBtn = await screen.findByRole("button", {
            name: "Annulla",
        });
        await userEvent.click(cancelBtn);

        // Immediate projection: badge becomes Annullamento… and Annulla disappears without waiting for poll
        await waitFor(() => expect(cancelCalled).toBe(true));
        await waitFor(() =>
            expect(screen.getByText("Annullamento…")).toBeInTheDocument(),
        );
        // Detail panel should now show intermediate cancelling message and hide the button
        await waitFor(() =>
            expect(
                screen.getByText("Annullamento in corso…"),
            ).toBeInTheDocument(),
        );
        expect(
            screen.queryByRole("button", { name: "Annulla" }),
        ).not.toBeInTheDocument();
    });
});
