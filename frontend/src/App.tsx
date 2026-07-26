import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthGuard } from '@/components/AuthGuard'
import { ComponentGallery } from '@/pages/ComponentGallery'
import { Catalog } from '@/pages/Catalog'
import { Login } from '@/pages/Login'

const queryClient = new QueryClient()

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/catalog"
            element={
              <AuthGuard>
                <Catalog />
              </AuthGuard>
            }
          />
          <Route path="/dev/components" element={<ComponentGallery />} />
          <Route path="*" element={<Navigate to="/catalog" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
