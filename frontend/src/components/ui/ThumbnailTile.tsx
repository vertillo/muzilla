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
        style={{
          borderRadius: 'var(--radius-sm)',
          objectFit: 'cover',
          border: '1px solid var(--border-subtle)',
        }}
      />
    )
  }
  const stripe =
    'repeating-linear-gradient(135deg, var(--bg-surface-raised), var(--bg-surface-raised) 4px, var(--bg-surface) 4px, var(--bg-surface) 8px)'
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: 'var(--radius-sm)',
        border: '1px solid var(--border-subtle)',
        background: stripe,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 4,
        textAlign: 'center',
      }}
    >
      <span
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 8,
          color: 'var(--text-muted)',
          lineHeight: 1.3,
        }}
      >
        {label}
      </span>
    </div>
  )
}
