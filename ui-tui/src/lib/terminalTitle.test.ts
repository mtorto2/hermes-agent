import { describe, expect, it } from 'vitest'

import { buildHermesTerminalTitle, shouldUseHermesTerminalTitle } from './terminalTitle.js'

describe('buildHermesTerminalTitle', () => {
  it('prefixes the model title with the stable Agent Lights slot letter', () => {
    expect(
      buildHermesTerminalTitle({
        cwd: '/Users/matt/Dropbox/CLIENTS/SAVANT SOFTWARE SYSTEMS/DEV/hermes-agent-dev',
        marker: '⏳',
        model: 'openai-codex/gpt-5.5',
        slot: '2',
      })
    ).toBe('⏳ B · GPT-5.5 · hermes-agent-dev')
  })

  it('renders Claude Opus models as Opus version labels', () => {
    expect(
      buildHermesTerminalTitle({
        cwd: '/tmp/project',
        marker: '✓',
        model: 'anthropic/claude-opus-4.8',
        slot: '4',
      })
    ).toBe('✓ D · Opus 4.8 · project')
  })

  it('omits the slot letter when the Agent Lights slot is not set', () => {
    expect(buildHermesTerminalTitle({ cwd: '/tmp/project', marker: '✓', model: 'gpt-5.5' })).toBe(
      '✓ GPT-5.5 · project'
    )
  })
})

describe('shouldUseHermesTerminalTitle', () => {
  it('disables TUI OSC title writes when Agent Lights owns the slot title', () => {
    expect(shouldUseHermesTerminalTitle('1')).toBe(false)
    expect(shouldUseHermesTerminalTitle(4)).toBe(false)
  })

  it('disables all TUI OSC title writes inside macOS Terminal.app', () => {
    expect(shouldUseHermesTerminalTitle(undefined, 'Apple_Terminal')).toBe(false)
    expect(shouldUseHermesTerminalTitle('', 'Apple_Terminal')).toBe(false)
    expect(shouldUseHermesTerminalTitle('not-a-slot', 'Apple_Terminal')).toBe(false)
  })

  it('keeps TUI terminal titles for non-Agent-Lights sessions outside Terminal.app', () => {
    expect(shouldUseHermesTerminalTitle(undefined, 'iTerm.app')).toBe(true)
    expect(shouldUseHermesTerminalTitle('', 'iTerm.app')).toBe(true)
    expect(shouldUseHermesTerminalTitle('not-a-slot', 'iTerm.app')).toBe(true)
  })
})
