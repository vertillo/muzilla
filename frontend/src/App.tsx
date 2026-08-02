import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AppShell } from '@/components/AppShell'
import { AuthGuard } from '@/components/AuthGuard'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { ApiError } from '@/lib/api'
import { pushToast, ToastProvider } from '@/hooks/useToasts'
import { ComponentGallery } from '@/pages/ComponentGallery'
import { Catalog } from '@/pages/Catalog'
import { ChangesList } from '@/pages/ChangesList'
import { ChangeSetReview } from '@/pages/ChangeSetReview'
import { Dashboard } from '@/pages/Dashboard'
import { Duplicates } from '@/pages/Duplicates'
import { GroupDetail } from '@/pages/GroupDetail'
import { Groups } from '@/pages/Groups'
import { ImportReview } from '@/pages/ImportReview'
import { ImportWizard } from '@/pages/ImportWizard'
import { Jobs } from '@/pages/Jobs'
import { Login } from '@/pages/Login'
import { RenameTracks } from '@/pages/RenameTracks'
import { ReviewManualSearch } from '@/pages/ReviewManualSearch'
import { Settings } from '@/pages/Settings'
import { TagEditor } from '@/pages/TagEditor'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
    },
  },
  // docs/PLAN.md §12e step 5.4: useToasts exists but only four call
  // sites used it, so usePinGroup/useMergeGroups/useRunCascade/
  // patchDecisions (and every other mutation) failed silently. One
  // cache-level handler covers all of them at once rather than adding
  // an onError to each hook individually; mutations that already show
  // a specific inline error (useLogin, useStartImport) opt out via
  // meta.suppressErrorToast so the user doesn't see the same failure
  // reported twice.
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => {
      if (mutation.meta?.suppressErrorToast) return
      const message = error instanceof ApiError ? error.message : 'Something went wrong.'
      pushToast({ tone: 'error', title: 'Action failed', description: message })
    },
  }),
})

function ProtectedShell() {
  return (
    <AuthGuard>
      <AppShell />
    </AuthGuard>
  )
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ErrorBoundary>
          <BrowserRouter>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route element={<ProtectedShell />}>
                <Route path="/" element={<Dashboard />} />
                <Route path="/catalog" element={<Catalog />} />
                <Route path="/edit" element={<TagEditor />} />
                <Route path="/rename" element={<RenameTracks />} />
                <Route path="/groups" element={<Groups />} />
                <Route path="/groups/:id" element={<GroupDetail />} />
                <Route path="/changes" element={<ChangesList />} />
                <Route path="/changes/:id" element={<ChangeSetReview />} />
                <Route path="/reviews/:id" element={<ReviewManualSearch />} />
                <Route path="/jobs" element={<Jobs />} />
                <Route path="/duplicates" element={<Duplicates />} />
                <Route path="/import" element={<ImportWizard />} />
                <Route path="/import/:sessionId" element={<ImportReview />} />
                <Route path="/settings" element={<Settings />} />
              </Route>
              <Route path="/dev/components" element={<ComponentGallery />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </BrowserRouter>
        </ErrorBoundary>
      </ToastProvider>
    </QueryClientProvider>
  )
}
