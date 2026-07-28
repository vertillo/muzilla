import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { SkeletonRows } from '@/components/ui/SkeletonRow'

describe('SkeletonRows', () => {
  it('renders 8 rows by default', () => {
    const { container } = render(<SkeletonRows />)
    expect(container.querySelectorAll(':scope > div > div').length).toBe(8)
  })

  it('renders the requested count', () => {
    const { container } = render(<SkeletonRows count={3} />)
    expect(container.querySelectorAll(':scope > div > div').length).toBe(3)
  })

  it('renders zero rows for count=0 without crashing', () => {
    const { container } = render(<SkeletonRows count={0} />)
    expect(container.querySelectorAll(':scope > div > div').length).toBe(0)
  })
})
