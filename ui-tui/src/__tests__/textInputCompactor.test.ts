import { describe, expect, it } from 'vitest'

import {
  canRunInputCompactor,
  inputCompactionNotice,
  inputCompactionSource,
  isInputCompactionKey,
  isAltTInputCompactionRaw,
  refreshInputCompactionOffset,
  spliceInputCompactionResult
} from '../components/textInput.js'

describe('input compactor helpers', () => {
  it('uses only the uncompacted tail for Ctrl+T segment compaction', () => {
    expect(inputCompactionSource('already compacted. new ramble', 19, false)).toEqual({ start: 19, text: 'new ramble' })
  })

  it('uses the whole buffer for Alt+T compaction', () => {
    expect(inputCompactionSource('already compacted. new ramble', 19, true)).toEqual({
      start: 0,
      text: 'already compacted. new ramble'
    })
  })

  it('skips empty drafts, slash commands, and already-compacted tails', () => {
    expect(inputCompactionSource('   ', 0, false)).toBeNull()
    expect(inputCompactionSource('/model gpt-5', 0, false)).toBeNull()
    expect(inputCompactionNotice('  /model gpt-5')).toBe('Input compactor skipped slash command.')
    expect(inputCompactionSource('done', 4, false)).toBeNull()
  })

  it('splices compacted output back into the original prefix', () => {
    expect(spliceInputCompactionResult('first part raw second part', 11, 'tight second')).toEqual({
      compactedUpTo: 'first part tight second'.length,
      cursor: 'first part tight second'.length,
      text: 'first part tight second'
    })
  })

  it('recognizes lowercase and uppercase Alt+T escape sequences', () => {
    expect(isAltTInputCompactionRaw('\x1bt')).toBe(true)
    expect(isAltTInputCompactionRaw('\x1bT')).toBe(true)
    expect(isAltTInputCompactionRaw('t')).toBe(false)
    expect(isInputCompactionKey('t', { ctrl: true } as any, 't')).toEqual({ wholeBuffer: false })
    expect(isInputCompactionKey('t', { meta: true } as any, 't')).toEqual({ wholeBuffer: true })
  })



  it('only enables compaction for explicitly enabled unmasked inputs', () => {
    expect(canRunInputCompactor(false)).toBe(false)
    expect(canRunInputCompactor(true)).toBe(true)
    expect(canRunInputCompactor(true, '*')).toBe(false)
  })

  it('preserves segment boundary for appended text and resets when prefix changes', () => {
    const prefix = 'first compacted'

    expect(refreshInputCompactionOffset('first compacted and more', prefix, prefix.length)).toEqual({
      compactedPrefix: prefix,
      compactedUpTo: prefix.length
    })
    expect(refreshInputCompactionOffset('edited compacted and more', prefix, prefix.length)).toEqual({
      compactedPrefix: '',
      compactedUpTo: 0
    })
  })
})
