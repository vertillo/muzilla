import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CandidatePicker } from '@/components/CandidatePicker'
import type { MatchProposal } from '@/lib/types'

const mocks = vi.hoisted(() => ({
  useCandidates: vi.fn(),
  useStageMatch: vi.fn(),
}))

vi.mock('@/hooks/useMatching', () => mocks)

const trackMatch: MatchProposal = {
  candidates: [
    {
      source: 'deezer',
      ref_id: '700',
      album: 'Twilight',
      album_artist: 'Piki',
      year: null,
      label: null,
      catalog_number: null,
      track_count: null,
      candidate_type: 'track',
      representative_title: 'Twilight Twilight',
      representative_artist: 'Piki',
      representative_position: 3,
      representative_duration_ms: 241000,
      cover_url: null,
      distance: 0.1,
      adjusted_distance: 0.1,
      score_signals: [],
      is_duplicate_of: [],
      corroborated_by: [],
    },
  ],
  auto_applicable: false,
  needs_confirmation: true,
  provider_outcomes: [],
  rejection_reason: null,
}

describe('CandidatePicker', () => {
  beforeEach(() => {
    mocks.useCandidates.mockReturnValue({ data: trackMatch, isLoading: false })
    mocks.useStageMatch.mockReturnValue({ mutate: vi.fn(), isPending: false })
  })

  it('renders unknown track counts and the representative track identity', () => {
    render(
      <CandidatePicker
        scopeType="track"
        scopeId={1}
        currentCandidateSource={null}
        currentCandidateRef={null}
      />,
    )

    expect(
      screen.getByText((_, element) => element?.textContent === 'Piki · Track count unknown'),
    ).toBeInTheDocument()
    expect(screen.queryByText('null tracks')).not.toBeInTheDocument()
    expect(
      screen.getByText((_, element) => element?.textContent === 'Track match: Twilight Twilight — Piki'),
    ).toBeInTheDocument()
  })
})
