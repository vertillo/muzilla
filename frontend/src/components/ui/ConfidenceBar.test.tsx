import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ConfidenceBar } from '@/components/ui/ConfidenceBar'

describe('ConfidenceBar', () => {
  it('defaults to 0% / low when no value is given', () => {
    render(<ConfidenceBar />)
    expect(screen.getByText('0%')).toBeInTheDocument()
    expect(screen.getByLabelText('confidence: low')).toBeInTheDocument()
  })

  it('is "low" just below the 50 threshold', () => {
    render(<ConfidenceBar value={49} />)
    expect(screen.getByLabelText('confidence: low')).toBeInTheDocument()
  })

  it('is "medium" exactly at the 50 threshold', () => {
    render(<ConfidenceBar value={50} />)
    expect(screen.getByLabelText('confidence: medium')).toBeInTheDocument()
  })

  it('is "medium" just below the 80 threshold', () => {
    render(<ConfidenceBar value={79} />)
    expect(screen.getByLabelText('confidence: medium')).toBeInTheDocument()
  })

  it('is "high" exactly at the 80 threshold', () => {
    render(<ConfidenceBar value={80} />)
    expect(screen.getByLabelText('confidence: high')).toBeInTheDocument()
  })

  it('is "high" at 100', () => {
    render(<ConfidenceBar value={100} />)
    expect(screen.getByLabelText('confidence: high')).toBeInTheDocument()
  })

  it('clamps a value above 100 down to 100%', () => {
    render(<ConfidenceBar value={150} />)
    expect(screen.getByText('100%')).toBeInTheDocument()
  })

  it('clamps a negative value up to 0%', () => {
    render(<ConfidenceBar value={-20} />)
    expect(screen.getByText('0%')).toBeInTheDocument()
  })
})
