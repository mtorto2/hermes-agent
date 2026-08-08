const SLOT_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

export interface HermesTerminalTitleInput {
  cwd?: string | null
  marker: string
  model?: string | null
  slot?: string | number | null
}

export function slotLetter(slot: string | number | null | undefined): string {
  const n = typeof slot === 'number' ? slot : Number.parseInt(String(slot ?? '').trim(), 10)

  if (!Number.isInteger(n) || n < 1 || n > SLOT_LETTERS.length) {
    return ''
  }

  return SLOT_LETTERS[n - 1] ?? ''
}

export function displayModelName(model: string | null | undefined): string {
  const raw = String(model ?? '').trim()

  if (!raw) {
    return ''
  }

  const leaf = raw.includes('/') ? (raw.split('/').pop() ?? raw) : raw
  const lower = leaf.toLowerCase()

  if (lower.startsWith('gpt-')) {
    return `GPT-${leaf.slice(4)}`
  }

  const claudeMatch = lower.match(/^claude-(sonnet|opus|haiku)-(.+)$/)

  if (claudeMatch) {
    return `${capitalize(claudeMatch[1] ?? '')} ${(claudeMatch[2] ?? '').replaceAll('-', '.')}`
  }

  const compactClaudeMatch = lower.match(/^claude-(\d+(?:[.-]\d+)*)$/)

  if (compactClaudeMatch) {
    return `Claude ${(compactClaudeMatch[1] ?? '').replaceAll('-', '.')}`
  }

  return leaf.replaceAll('_', ' ')
}

export function shouldUseHermesTerminalTitle(
  slot: string | number | null | undefined,
  termProgram: string | null | undefined = process.env.TERM_PROGRAM
): boolean {
  // Terminal.app treats OSC title updates as tab-title writes and they can
  // clobber a human's Terminal Inspector (⌘I) custom tab title on every
  // busy/idle transition.  Agent Lights can still apply compact fallback slot
  // labels for Terminal.app tabs when no manual Inspector title is present, so
  // the TUI should stay completely out of Terminal.app tab naming.
  if (String(termProgram ?? '').trim() === 'Apple_Terminal') {
    return false
  }

  // Outside Terminal.app, Agent Lights owns slot-backed tab naming.  Leave
  // those untouched while preserving TUI title updates for ordinary terminals.
  return !slotLetter(slot)
}

export function buildHermesTerminalTitle({ cwd, marker, model, slot }: HermesTerminalTitleInput): string {
  const modelLabel = displayModelName(model)
  const letter = slotLetter(slot)
  const markerAndLetter = letter ? [marker, letter].filter(Boolean).join(' ') : marker

  const leading = letter
    ? [markerAndLetter, modelLabel].filter(Boolean).join(' · ')
    : [markerAndLetter, modelLabel].filter(Boolean).join(' ')

  const cwdLabel = cwd ? basename(cwd) : ''

  return [leading || 'Hermes', cwdLabel].filter(Boolean).join(' · ')
}

function basename(path: string): string {
  const trimmed = path.trim().replace(/\/+$/, '')

  if (!trimmed) {
    return ''
  }

  return trimmed.split('/').pop() || trimmed
}

function capitalize(value: string): string {
  return value ? value[0]!.toUpperCase() + value.slice(1) : value
}
