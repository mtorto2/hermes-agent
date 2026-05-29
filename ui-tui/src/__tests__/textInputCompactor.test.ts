import { EventEmitter } from 'node:events'

import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  canRunInputCompactor,
  inputCompactionNotice,
  inputCompactionSource,
  isAltTInputCompactionRaw,
  isInputCompactionKey,
  refreshInputCompactionOffset,
  runInputCompactorProcess,
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


class FakeStream extends EventEmitter {
  chunks: string[] = []
  encoding = ''
  endedWith = ''

  setEncoding(encoding: string) {
    this.encoding = encoding
  }

  end(text: string) {
    this.endedWith = text
  }
}

class FakeChild extends EventEmitter {
  exitCode: number | null = null
  killedSignals: string[] = []
  stderr = new FakeStream()
  stdin = new FakeStream()
  stdout = new FakeStream()

  kill(signal: string) {
    this.killedSignals.push(signal)

    return true
  }
}

describe('runInputCompactorProcess', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('writes source text to stdin and returns compacted stdout on clean close', () => {
    const child = new FakeChild()
    const onFailure = vi.fn()
    const onSettled = vi.fn()
    const onSuccess = vi.fn()

    runInputCompactorProcess({
      compactorPath: '/tmp/compact',
      onFailure,
      onSettled,
      onSuccess,
      sourceText: 'raw dictation',
      spawnImpl: vi.fn(() => child as any)
    })

    child.stdout.emit('data', 'tight request\n')
    child.emit('close', 0)

    expect(child.stdin.endedWith).toBe('raw dictation')
    expect(onSuccess).toHaveBeenCalledWith('tight request')
    expect(onFailure).not.toHaveBeenCalled()
    expect(onSettled).toHaveBeenCalledTimes(1)
  })

  it('reports subprocess stderr on non-zero close', () => {
    const child = new FakeChild()
    const onFailure = vi.fn()

    runInputCompactorProcess({
      compactorPath: '/tmp/compact',
      onFailure,
      onSettled: vi.fn(),
      onSuccess: vi.fn(),
      sourceText: 'raw dictation',
      spawnImpl: vi.fn(() => child as any)
    })

    child.stderr.emit('data', 'first\nlast error')

    child.emit('close', 2)

    expect(onFailure).toHaveBeenCalledWith('Input compactor failed: last error')
  })

  it('sends SIGTERM on timeout, escalates to SIGKILL, and clears kill timer on close', () => {
    vi.useFakeTimers()
    const child = new FakeChild()
    const onFailure = vi.fn()
    const onSettled = vi.fn()

    runInputCompactorProcess({
      compactorPath: '/tmp/compact',
      killGraceMs: 25,
      onFailure,
      onSettled,
      onSuccess: vi.fn(),
      sourceText: 'raw dictation',
      spawnImpl: vi.fn(() => child as any),
      timeoutMs: 50
    })

    vi.advanceTimersByTime(50)
    expect(child.killedSignals).toEqual(['SIGTERM'])
    expect(onFailure).toHaveBeenCalledWith('Input compactor failed: timed out after 90 seconds')
    expect(onSettled).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(25)
    expect(child.killedSignals).toEqual(['SIGTERM', 'SIGKILL'])

    const exitsAfterTerm = new FakeChild()
    runInputCompactorProcess({
      compactorPath: '/tmp/compact',
      killGraceMs: 25,
      onFailure: vi.fn(),
      onSettled: vi.fn(),
      onSuccess: vi.fn(),
      sourceText: 'raw dictation',
      spawnImpl: vi.fn(() => exitsAfterTerm as any),
      timeoutMs: 50
    })

    vi.advanceTimersByTime(50)
    exitsAfterTerm.exitCode = 143
    exitsAfterTerm.emit('close', 143)
    vi.advanceTimersByTime(25)
    expect(exitsAfterTerm.killedSignals).toEqual(['SIGTERM'])
  })
})
