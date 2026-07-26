import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ComponentGallery } from '@/pages/ComponentGallery'

const queryClient = new QueryClient()

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/dev/components" element={<ComponentGallery />} />
          <Route path="*" element={<Navigate to="/dev/components" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
