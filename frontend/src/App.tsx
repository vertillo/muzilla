import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthGuard } from '@/components/AuthGuard'
import { ComponentGallery } from '@/pages/ComponentGallery'
import { Catalog } from '@/pages/Catalog'
import { ChangesList } from '@/pages/ChangesList'
import { ChangeSetReview } from '@/pages/ChangeSetReview'
import { GroupDetail } from '@/pages/GroupDetail'
import { Groups } from '@/pages/Groups'
import { Login } from '@/pages/Login'
import { TagEditor } from '@/pages/TagEditor'

const queryClient = new QueryClient()

function Protected({ children }: { children: ReactNode }) {
  return <AuthGuard>{children}</AuthGuard>
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/catalog"
            element={
              <Protected>
                <Catalog />
              </Protected>
            }
          />
          <Route
            path="/edit"
            element={
              <Protected>
                <TagEditor />
              </Protected>
            }
          />
          <Route
            path="/groups"
            element={
              <Protected>
                <Groups />
              </Protected>
            }
          />
          <Route
            path="/groups/:id"
            element={
              <Protected>
                <GroupDetail />
              </Protected>
            }
          />
          <Route
            path="/changes"
            element={
              <Protected>
                <ChangesList />
              </Protected>
            }
          />
          <Route
            path="/changes/:id"
            element={
              <Protected>
                <ChangeSetReview />
              </Protected>
            }
          />
          <Route path="/dev/components" element={<ComponentGallery />} />
          <Route path="*" element={<Navigate to="/catalog" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
