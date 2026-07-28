import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AppShell } from '@/components/AppShell'
import { AuthGuard } from '@/components/AuthGuard'
import { ToastProvider } from '@/hooks/useToasts'
import { ComponentGallery } from '@/pages/ComponentGallery'
import { Catalog } from '@/pages/Catalog'
import { ChangesList } from '@/pages/ChangesList'
import { ChangeSetReview } from '@/pages/ChangeSetReview'
import { Duplicates } from '@/pages/Duplicates'
import { GroupDetail } from '@/pages/GroupDetail'
import { Groups } from '@/pages/Groups'
import { ImportReview } from '@/pages/ImportReview'
import { ImportWizard } from '@/pages/ImportWizard'
import { Jobs } from '@/pages/Jobs'
import { Login } from '@/pages/Login'
import { RenameTracks } from '@/pages/RenameTracks'
import { TagEditor } from '@/pages/TagEditor'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
    },
  },
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
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<ProtectedShell />}>
              <Route path="/catalog" element={<Catalog />} />
              <Route path="/edit" element={<TagEditor />} />
              <Route path="/rename" element={<RenameTracks />} />
              <Route path="/groups" element={<Groups />} />
              <Route path="/groups/:id" element={<GroupDetail />} />
              <Route path="/changes" element={<ChangesList />} />
              <Route path="/changes/:id" element={<ChangeSetReview />} />
              <Route path="/jobs" element={<Jobs />} />
              <Route path="/duplicates" element={<Duplicates />} />
              <Route path="/import" element={<ImportWizard />} />
              <Route path="/import/:sessionId" element={<ImportReview />} />
            </Route>
            <Route path="/dev/components" element={<ComponentGallery />} />
            <Route path="*" element={<Navigate to="/catalog" replace />} />
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}
