import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Badge, type BadgeTone } from '@/components/ui/Badge'

const ALL_TONES: BadgeTone[] = [
  'added',
  'removed',
  'conflict',
  'unchanged',
  'musicbrainz',
  'discogs',
  'deezer',
  'accent',
  'neutral',
]

describe('Badge', () => {
  it('defaults to the neutral tone', () => {
    render(<Badge>text</Badge>)
    expect(screen.getByText('text')).toHaveStyle({ color: 'var(--text-secondary)' })
  })

  it.each(ALL_TONES)('renders the %s tone with a distinct color', (tone) => {
    render(<Badge tone={tone}>{tone}</Badge>)
    const el = screen.getByText(tone)
    expect(el.style.color).not.toBe('')
    expect(el.style.background).not.toBe('')
  })

  it('renders a leading dot only when dot=true', () => {
    const { container: withoutDot } = render(<Badge tone="added">x</Badge>)
    expect(withoutDot.querySelectorAll('span').length).toBe(1)

    const { container: withDot } = render(<Badge tone="added" dot>x</Badge>)
    expect(withDot.querySelectorAll('span').length).toBe(2)
  })

  it('the dot inherits the tone color, never relying on hue alone without a paired glyph slot', () => {
    const { container } = render(<Badge tone="removed" dot>removed</Badge>)
    const dot = container.querySelector('span > span')
    expect(dot).not.toBeNull()
    expect(dot).toHaveStyle({ background: 'var(--diff-removed)' })
  })
})
