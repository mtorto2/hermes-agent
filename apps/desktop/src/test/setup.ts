class MemoryStorage implements Storage {
  private readonly store = new Map<string, string>()

  get length(): number {
    return this.store.size
  }

  clear(): void {
    this.store.clear()
  }

  getItem(key: string): string | null {
    return this.store.has(key) ? this.store.get(key)! : null
  }

  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null
  }

  removeItem(key: string): void {
    this.store.delete(key)
  }

  setItem(key: string, value: string): void {
    this.store.set(key, String(value))
  }
}

function ensureStorage(name: 'localStorage' | 'sessionStorage'): void {
  const current = window[name]

  if (
    typeof current?.getItem === 'function' &&
    typeof current?.setItem === 'function' &&
    typeof current?.clear === 'function'
  ) {
    return
  }

  Object.defineProperty(window, name, {
    configurable: true,
    value: new MemoryStorage()
  })
}

ensureStorage('localStorage')
ensureStorage('sessionStorage')

if (!globalThis.CSS) {
  Object.defineProperty(globalThis, 'CSS', {
    configurable: true,
    value: {}
  })
}

if (typeof globalThis.CSS.escape !== 'function') {
  Object.defineProperty(globalThis.CSS, 'escape', {
    configurable: true,
    value: (value: string) =>
      String(value).replace(/[^a-zA-Z0-9_-]/g, character => `\\${character.codePointAt(0)?.toString(16)} `)
  })
}
