import { describe, expect, it } from 'vitest'

import { composerPromptText, profilePromptLabel } from '../lib/prompt.js'

describe('composerPromptText', () => {
  it('returns shell prompt for ! commands', () => {
    expect(composerPromptText('❯', 'coder', true)).toBe('$')
  })

  it('prefixes named profiles onto the normal prompt', () => {
    expect(composerPromptText('❯', 'coder')).toBe('coder ❯')
  })

  it('shows Matt lane names for business and personal profiles', () => {
    expect(profilePromptLabel('business')).toBe('Tate')
    expect(profilePromptLabel('tate')).toBe('Tate')
    expect(profilePromptLabel('personal')).toBe('Aurelius')
    expect(profilePromptLabel('aurelius')).toBe('Aurelius')
    expect(composerPromptText('❯', 'business')).toBe('Tate ❯')
    expect(composerPromptText('❯', 'personal')).toBe('Aurelius ❯')
  })

  it('does not prefix default or custom profiles', () => {
    expect(composerPromptText('❯', 'default')).toBe('❯')
    expect(composerPromptText('❯', 'custom')).toBe('❯')
    expect(composerPromptText('❯')).toBe('❯')
  })

  it('uses a Termux-safe ASCII prompt marker in normal mode', () => {
    expect(composerPromptText('❯', 'coder', false, true, 50)).toBe('>')
  })

  it('keeps profile prefix suppressed on narrow Termux widths', () => {
    expect(composerPromptText('❯', 'upstr', false, true, 72)).toBe('>')
  })

  it('allows profile prefix on very wide Termux panes', () => {
    expect(composerPromptText('❯', 'upstr', false, true, 120)).toBe('upstr >')
    expect(composerPromptText('❯', 'business', false, true, 120)).toBe('Tate >')
    expect(composerPromptText('❯', 'personal', false, true, 120)).toBe('Aurelius >')
  })
})
