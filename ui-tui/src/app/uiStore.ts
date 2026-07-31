import { atom, computed } from 'nanostores'

import { MOUSE_TRACKING } from '../config/env.js'
import { ZERO } from '../domain/usage.js'
import { bootTheme } from '../lib/themeBoot.js'
import { DEFAULT_THEME, fromSkin } from '../theme.js'

import { DEFAULT_INDICATOR_STYLE, type UiState } from './interfaces.js'

// The profile launcher supplies an exact serialized skin. It must win over a
// prior terminal boot cache so profiles cannot briefly inherit another
// profile's brand. The cache remains the flash-free fallback for ordinary
// launches without an explicit skin.
const initialTheme = () => {
  const raw = process.env.HERMES_TUI_INITIAL_SKIN

  if (!raw) {
    return bootTheme ?? DEFAULT_THEME
  }

  try {
    const skin = JSON.parse(raw) as {
      banner_hero?: string
      banner_logo?: string
      branding?: Record<string, string>
      colors?: Record<string, string>
      help_header?: string
      tool_prefix?: string
    }

    return fromSkin(
      skin.colors ?? {},
      skin.branding ?? {},
      skin.banner_logo ?? '',
      skin.banner_hero ?? '',
      skin.tool_prefix ?? '',
      skin.help_header ?? ''
    )
  } catch {
    return bootTheme ?? DEFAULT_THEME
  }
}

const buildUiState = (): UiState => {
  const theme = initialTheme()

  return {
    battery: false,
    batteryStatus: null,
    bgTasks: new Set(),
    busy: false,
    busyInputMode: 'queue',
    compact: false,
    detailsMode: 'collapsed',
    detailsModeCommandOverride: false,
    focusView: false,
    indicatorStyle: DEFAULT_INDICATOR_STYLE,
    info: null,
    liveSessionCount: 0,
    inlineDiffs: true,
    mouseTracking: MOUSE_TRACKING,
    notice: null,
    pasteCollapseLines: 5,
    pasteCollapseChars: 2000,
    sections: {},
    sessionTitle: '',
    showReasoning: false,
    sid: null,
    status: `summoning ${theme.brand.name.toLowerCase()}…`,
    statusBar: 'top',
    streaming: true,
    theme,
    usage: ZERO
  }
}

export const $uiState = atom<UiState>(buildUiState())

export const $uiTheme = computed($uiState, state => state.theme)
export const $uiSessionId = computed($uiState, state => state.sid)

export const getUiState = () => $uiState.get()

export const patchUiState = (next: Partial<UiState> | ((state: UiState) => UiState)) =>
  $uiState.set(typeof next === 'function' ? next($uiState.get()) : { ...$uiState.get(), ...next })

export const resetUiState = () => $uiState.set(buildUiState())
