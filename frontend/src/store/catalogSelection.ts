import { create } from 'zustand'

interface CatalogSelectionState {
  selected: Set<number>
  toggle: (trackId: number) => void
  clear: () => void
}

// docs/product-spec.md: selecting 40 tracks, opening bulk
// edit, and returning cleared everything — Catalog.tsx held selection
// in component state, which unmounts on navigation. Lifted into a
// store (already a dependency; store/auth.ts is the pattern followed
// here) so it survives the round trip to /edit or /rename and back.
export const useCatalogSelectionStore = create<CatalogSelectionState>((set) => ({
  selected: new Set(),
  toggle: (trackId) =>
    set((state) => {
      const next = new Set(state.selected)
      if (next.has(trackId)) next.delete(trackId)
      else next.add(trackId)
      return { selected: next }
    }),
  clear: () => set({ selected: new Set() }),
}))
