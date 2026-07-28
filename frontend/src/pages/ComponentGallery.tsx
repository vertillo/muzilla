import { useState } from 'react'
import {
  Button,
  Badge,
  type BadgeTone,
  ConfidenceBar,
  TableRow,
  type TableRowState,
  ThumbnailTile,
  ThreeStateToggle,
  type ToggleValue,
  Input,
  Select,
  Checkbox,
  Modal,
  Toast,
  type ToastTone,
  ProgressBar,
  EmptyState,
} from '@/components/ui'

const BADGE_TONES: BadgeTone[] = [
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
const TOAST_TONES: ToastTone[] = ['success', 'error', 'warning', 'info']
const ROW_STATES: TableRowState[] = ['default', 'selected', 'conflicted']

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-9">
      <h2 className="font-mono text-xs uppercase tracking-wide text-text-muted border-b border-border-subtle pb-3 mb-5">
        {title}
      </h2>
      <div className="flex flex-wrap gap-5 items-start">{children}</div>
    </section>
  )
}

export function ComponentGallery() {
  const [theme, setTheme] = useState<'dark' | 'light'>('dark')
  const [toggleValue, setToggleValue] = useState<ToggleValue>('pending')
  const [checked, setChecked] = useState(true)
  const [inputValue, setInputValue] = useState('Sigur Rós')
  const [modalOpen, setModalOpen] = useState(false)

  return (
    <div
      data-theme={theme}
      className="min-h-screen bg-canvas text-text-primary font-sans p-8"
    >
      <div className="flex justify-between items-center mb-8">
        <h1 className="text-2xl font-semibold m-0">muzilla component gallery</h1>
        <Button variant="secondary" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
          Switch to {theme === 'dark' ? 'light' : 'dark'} theme
        </Button>
      </div>

      <Section title="Buttons">
        <Button variant="primary">Primary</Button>
        <Button variant="secondary">Secondary</Button>
        <Button variant="ghost">Ghost</Button>
        <Button variant="destructive">Destructive</Button>
        <Button variant="primary" disabled>
          Disabled
        </Button>
        <Button variant="primary" size="sm">
          Small
        </Button>
      </Section>

      <Section title="Badges (all tones, with dot)">
        {BADGE_TONES.map((tone) => (
          <Badge key={tone} tone={tone} dot>
            {tone}
          </Badge>
        ))}
      </Section>

      <Section title="Confidence bar">
        <ConfidenceBar value={95} />
        <ConfidenceBar value={65} />
        <ConfidenceBar value={20} />
      </Section>

      <Section title="Three-state toggle">
        <ThreeStateToggle value={toggleValue} onChange={setToggleValue} />
        <ThreeStateToggle value="accept" disabled />
      </Section>

      <Section title="Thumbnail tile">
        <ThumbnailTile />
        <ThumbnailTile size={80} />
      </Section>

      <Section title="Form primitives">
        <div className="w-[220px]">
          <Input value={inputValue} onChange={setInputValue} placeholder="Track title" />
        </div>
        <div className="w-[180px]">
          <Select
            value="mb"
            options={[
              { value: 'mb', label: 'MusicBrainz' },
              { value: 'discogs', label: 'Discogs' },
              { value: 'deezer', label: 'Deezer' },
            ]}
            onChange={() => {}}
          />
        </div>
        <Checkbox checked={checked} onChange={setChecked} label="Accept all non-destructive" />
        <Checkbox indeterminate label="Partially selected" />
      </Section>

      <Section title="Table rows">
        <div className="w-[420px] border border-border-subtle rounded-[6px] overflow-hidden">
          {ROW_STATES.map((s) => (
            <TableRow key={s} state={s}>
              <span className="font-mono text-2xs text-text-muted">{s}</span>
              <span>Svefn-g-englar</span>
            </TableRow>
          ))}
        </div>
      </Section>

      <Section title="Progress bar">
        <div className="w-[260px]">
          <ProgressBar value={62} label="Scanning library" />
        </div>
      </Section>

      <Section title="Toasts">
        {TOAST_TONES.map((tone) => (
          <Toast key={tone} tone={tone} title={`${tone} toast`} description="Example description text." />
        ))}
      </Section>

      <Section title="Empty state">
        <div className="w-[360px] border border-border-subtle rounded-lg">
          <EmptyState title="No tracks found" description="Try adjusting your filters or scan a library." />
        </div>
      </Section>

      <Section title="Modal">
        <Button variant="secondary" onClick={() => setModalOpen(true)}>
          Open modal
        </Button>
        <div className="relative w-0 h-0">
          <Modal
            open={modalOpen}
            title="Apply 14 changes?"
            onClose={() => setModalOpen(false)}
            footer={
              <>
                <Button variant="ghost" onClick={() => setModalOpen(false)}>
                  Cancel
                </Button>
                <Button variant="primary" onClick={() => setModalOpen(false)}>
                  Apply
                </Button>
              </>
            }
          >
            This will write tags to 14 tracks. The change can be undone afterward.
          </Modal>
        </div>
      </Section>
    </div>
  )
}
