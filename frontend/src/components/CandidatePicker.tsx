import { EmptyState } from '@/components/ui'

interface CandidatePickerProps {
  scopeType: string
  scopeId: number | null
  currentCandidateSource: string | null
  currentCandidateRef: string | null
  onStaged?: (changeSetId: number) => void
}

// Legacy ChangeSet candidate UI removed — ReviewBundle covers candidate selection.
// Stub preserved for any lingering imports; it renders no candidates.
export function CandidatePicker({ scopeId, scopeType }: CandidatePickerProps) {
  if (scopeId === null || scopeType !== 'track') {
    return (
      <EmptyState
        title="No candidates"
        description="Candidates are only available for changesets scoped to a group or track."
      />
    )
  }
  return (
    <EmptyState
      title="No candidates"
      description="Candidate selection is now handled via ReviewBundle. Open the review to choose a candidate."
    />
  )
}
