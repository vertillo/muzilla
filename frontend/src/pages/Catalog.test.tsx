import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { CatalogTable } from '@/pages/Catalog'
import type { TrackSummary } from '@/lib/types'

const STORAGE_KEY = 'muzilla.catalog.column-widths'

const COLUMN_BOUNDS = [
  { label: 'Titolo', min: 160, max: 420, defaultWidth: 240 },
  { label: 'Artista', min: 120, max: 320, defaultWidth: 180 },
  { label: 'Album', min: 140, max: 360, defaultWidth: 220 },
  { label: 'Aggiunto', min: 96, max: 200, defaultWidth: 120 },
  { label: 'Formato', min: 88, max: 180, defaultWidth: 110 },
  { label: 'Durata', min: 88, max: 180, defaultWidth: 110 },
  { label: 'Stato', min: 188, max: 360, defaultWidth: 220 },
] as const

const TRACK: TrackSummary = {
  id: 1,
  path: '/music/track.mp3',
  filename: 'track.mp3',
  ext: 'mp3',
  title: 'Track',
  artist: 'Artist',
  album: 'Album',
  album_artist: 'Artist',
  track_no: 1,
  disc_no: 1,
  year: 2024,
  genre: ['Rock'],
  duration_ms: 180_000,
  format: 'mp3',
  bitrate: 320,
  has_embedded_art: true,
  has_lyrics: false,
  probe_error: null,
  missing_since: null,
}

function renderTable(onSort = vi.fn()) {
  return render(
    <MemoryRouter>
      <CatalogTable tracks={[TRACK]} sort="title" sortDirection="asc" onSort={onSort} />
    </MemoryRouter>,
  )
}

describe('CatalogTable', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it('renders every desktop column with an independent bounded resize control', () => {
    renderTable()

    expect(screen.getAllByRole('columnheader')).toHaveLength(COLUMN_BOUNDS.length)
    for (const { label, min, max, defaultWidth } of COLUMN_BOUNDS) {
      expect(screen.getByText(label, { selector: 'button, span' })).toBeInTheDocument()
      const resize = screen.getByRole('separator', { name: `Ridimensiona colonna ${label}` })
      expect(resize).toHaveAttribute('aria-valuemin', String(min))
      expect(resize).toHaveAttribute('aria-valuemax', String(max))
      expect(resize).toHaveAttribute('aria-valuenow', String(defaultWidth))
    }

    expect(screen.getByRole('button', { name: 'Ordina per Titolo' })).toBeInTheDocument()
  })

  it('clamps keyboard resizing at both bounds for every column', () => {
    renderTable()

    for (const { label, min, max } of COLUMN_BOUNDS) {
      const resize = screen.getByRole('separator', { name: `Ridimensiona colonna ${label}` })
      fireEvent.keyDown(resize, { key: 'Home' })
      expect(resize).toHaveAttribute('aria-valuenow', String(min))
      fireEvent.keyDown(resize, { key: 'ArrowLeft' })
      expect(resize).toHaveAttribute('aria-valuenow', String(min))
      fireEvent.keyDown(resize, { key: 'End' })
      expect(resize).toHaveAttribute('aria-valuenow', String(max))
      fireEvent.keyDown(resize, { key: 'ArrowRight' })
      expect(resize).toHaveAttribute('aria-valuenow', String(max))
    }
  })

  it('resizes with pointer and keyboard input without invoking sorting, then restores persisted widths', () => {
    const onSort = vi.fn()
    const first = renderTable(onSort)
    const titleResize = screen.getByRole('separator', { name: 'Ridimensiona colonna Titolo' })

    fireEvent.pointerDown(titleResize, { pointerId: 1, clientX: 100 })
    fireEvent.pointerMove(titleResize, { pointerId: 1, clientX: 135 })
    fireEvent.pointerUp(titleResize, { pointerId: 1, clientX: 135 })
    expect(titleResize).toHaveAttribute('aria-valuenow', '275')
    expect(onSort).not.toHaveBeenCalled()
    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '{}')).toMatchObject({ title: 275 })

    first.unmount()
    renderTable()
    const persisted = screen.getByRole('separator', { name: 'Ridimensiona colonna Titolo' })
    expect(persisted).toHaveAttribute('aria-valuenow', '275')

    fireEvent.keyDown(persisted, { key: 'End' })
    expect(persisted).toHaveAttribute('aria-valuenow', '420')
    fireEvent.keyDown(persisted, { key: 'ArrowRight' })
    expect(persisted).toHaveAttribute('aria-valuenow', '420')
    fireEvent.keyDown(persisted, { key: 'Home' })
    expect(persisted).toHaveAttribute('aria-valuenow', '160')

    fireEvent.click(screen.getByRole('button', { name: 'Ripristina larghezze colonne' }))
    expect(persisted).toHaveAttribute('aria-valuenow', '240')
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('falls back safely when local storage is malformed or unavailable for reads, writes, and removal', () => {
    window.localStorage.setItem(STORAGE_KEY, '{not-json')
    const malformed = renderTable()
    expect(screen.getByRole('separator', { name: 'Ridimensiona colonna Titolo' })).toHaveAttribute('aria-valuenow', '240')
    malformed.unmount()

    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage blocked')
    })
    let unavailableReadRender: ReturnType<typeof renderTable> | undefined
    expect(() => { unavailableReadRender = renderTable() }).not.toThrow()
    const unavailableRead = screen.getByRole('separator', { name: 'Ridimensiona colonna Titolo' })
    expect(unavailableRead).toHaveAttribute('aria-valuenow', '240')
    unavailableReadRender?.unmount()
    getItem.mockRestore()

    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage blocked')
    })
    const unavailableWriteRender = renderTable()
    const unavailableWrite = screen.getByRole('separator', { name: 'Ridimensiona colonna Titolo' })
    expect(() => fireEvent.keyDown(unavailableWrite, { key: 'ArrowRight' })).not.toThrow()
    expect(unavailableWrite).toHaveAttribute('aria-valuenow', '256')
    setItem.mockRestore()

    const removeItem = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('storage blocked')
    })
    expect(() => fireEvent.click(screen.getByRole('button', { name: 'Ripristina larghezze colonne' }))).not.toThrow()
    expect(screen.getByRole('separator', { name: 'Ridimensiona colonna Titolo' })).toHaveAttribute('aria-valuenow', '240')
    unavailableWriteRender.unmount()
    removeItem.mockRestore()
  })
})
