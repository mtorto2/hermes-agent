import { describe, expect, it } from 'vitest'

import { buildHermesTerminalTitle } from './terminalTitle.js'

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
