import * as React from 'react'

/**
 * Console connection settings: API key and (optional) API base URL.
 *
 * Both persist to localStorage ONLY — never sent anywhere but the configured
 * API host, never synced to a server. The Overview page must say so
 * explicitly next to the input; this module is where that promise is kept.
 */

const API_KEY_STORAGE_KEY = 'sodamem-console-api-key'
const BASE_URL_STORAGE_KEY = 'sodamem-console-base-url'

interface ConfigContextValue {
  apiKey: string
  setApiKey: (key: string) => void
  /** Empty string means "same origin as the console" (the normal case when
   * FastAPI mounts the console itself). */
  baseUrl: string
  setBaseUrl: (url: string) => void
}

const ConfigContext = React.createContext<ConfigContextValue | null>(null)

function readStorage(key: string): string {
  try {
    return window.localStorage.getItem(key) ?? ''
  } catch {
    // Storage can throw in locked-down browser contexts (private mode quota,
    // disabled storage). Degrade to in-memory only rather than crash.
    return ''
  }
}

function writeStorage(key: string, value: string): void {
  try {
    if (value) {
      window.localStorage.setItem(key, value)
    } else {
      window.localStorage.removeItem(key)
    }
  } catch {
    // Same as above: best-effort persistence, never fatal.
  }
}

export function ConfigProvider({ children }: { children: React.ReactNode }) {
  const [apiKey, setApiKeyState] = React.useState(() => readStorage(API_KEY_STORAGE_KEY))
  const [baseUrl, setBaseUrlState] = React.useState(() => readStorage(BASE_URL_STORAGE_KEY))

  const setApiKey = React.useCallback((key: string) => {
    setApiKeyState(key)
    writeStorage(API_KEY_STORAGE_KEY, key)
  }, [])

  const setBaseUrl = React.useCallback((url: string) => {
    const trimmed = url.trim().replace(/\/+$/, '')
    setBaseUrlState(trimmed)
    writeStorage(BASE_URL_STORAGE_KEY, trimmed)
  }, [])

  const value = React.useMemo(
    () => ({ apiKey, setApiKey, baseUrl, setBaseUrl }),
    [apiKey, setApiKey, baseUrl, setBaseUrl],
  )

  return <ConfigContext.Provider value={value}>{children}</ConfigContext.Provider>
}

export function useConfig(): ConfigContextValue {
  const ctx = React.useContext(ConfigContext)
  if (!ctx) throw new Error('useConfig must be used within ConfigProvider')
  return ctx
}
