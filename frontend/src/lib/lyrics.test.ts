import { describe, expect, it } from 'vitest'
import { lyricsText, withEditedLyricsText } from '@/lib/lyrics'

describe('lyrics payload editing', () => {
  it('edits text while preserving synced and provider instead of stringifying the object', () => {
    const current = { text: '[00:01] first line', synced: true, provider: 'lrclib' }

    expect(lyricsText(current)).toBe('[00:01] first line')
    expect(withEditedLyricsText(current, 'edited line')).toEqual({
      text: 'edited line',
      synced: true,
      provider: 'lrclib',
    })
  })
})
