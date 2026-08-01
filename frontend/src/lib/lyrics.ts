import type { LyricsValue } from '@/lib/types'

type LegacyLyricsValue = Omit<LyricsValue, 'provider'> & { provider?: string }

function isLyricsValue(value: unknown): value is LegacyLyricsValue {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as Record<string, unknown>).text === 'string' &&
    typeof (value as Record<string, unknown>).synced === 'boolean'
  )
}

/** Extract editable lyrics text without coercing a structured payload to `[object Object]`. */
export function lyricsText(value: unknown): string {
  return isLyricsValue(value) ? value.text : typeof value === 'string' ? value : ''
}

/** Preserve the legacy/current synced bit (and provider when present) during an edit. */
export function withEditedLyricsText(value: unknown, text: string): LegacyLyricsValue {
  if (isLyricsValue(value)) return { ...value, text }
  return { text, synced: false }
}
