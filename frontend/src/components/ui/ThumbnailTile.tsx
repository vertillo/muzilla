export interface ThumbnailTileProps {
  src?: string
  size?: number
  label?: string
}

export function ThumbnailTile({ src, size = 56, label = 'no artwork' }: ThumbnailTileProps) {
  if (src) {
    return (
      <img
        src={src}
        width={size}
        height={size}
        alt={label}
        className="rounded-sm object-cover border border-border-subtle"
      />
    )
  }
  const stripe =
    'repeating-linear-gradient(135deg, var(--bg-surface-raised), var(--bg-surface-raised) 4px, var(--bg-surface) 4px, var(--bg-surface) 8px)'
  return (
    <div
      className="rounded-sm border border-border-subtle flex items-center justify-center p-2 text-center"
      style={{ width: size, height: size, background: stripe }}
    >
      <span className="font-mono text-text-muted text-[8px] leading-[1.3]">{label}</span>
    </div>
  )
}
