import { EventEmitter } from 'events'
import React from 'react'
import { describe, expect, it } from 'vitest'

import Text from './components/Text.js'
import Ink from './ink.js'

class FakeTty extends EventEmitter {
  chunks: string[] = []
  columns = 20
  rows = 5
  isTTY = true

  write(chunk: string | Uint8Array, cb?: (err?: Error | null) => void): boolean {
    this.chunks.push(typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString('utf8'))
    cb?.()
    return true
  }
}

const tick = () => new Promise<void>(resolve => queueMicrotask(resolve))
const wait = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms))

describe('Ink resize healing', () => {
  it('repaints same-dimension alt-screen resize events', async () => {
    const stdout = new FakeTty()
    const stdin = new FakeTty()
    const stderr = new FakeTty()
    const ink = new Ink({
      exitOnCtrlC: false,
      patchConsole: false,
      stderr: stderr as unknown as NodeJS.WriteStream,
      stdin: stdin as unknown as NodeJS.ReadStream,
      stdout: stdout as unknown as NodeJS.WriteStream
    })

    ink.setAltScreenActive(true)
    ink.render(React.createElement(Text, null, 'hello'))
    ink.onRender()
    await tick()
    ink.onRender()
    stdout.chunks = []

    stdout.emit('resize')
    await tick()
    ink.onRender()
    await wait(180)
    ink.onRender()
    await tick()

    expect(stdout.chunks.join('')).toContain('hello')

    ink.unmount()
  })
})
