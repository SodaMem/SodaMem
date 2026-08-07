import * as React from 'react'

interface AsyncState<T> {
  data: T | null
  error: unknown
  loading: boolean
}

/** Runs `fn` on mount and whenever `deps` change. For data that loads
 * automatically (health check, etc). Call `reload()` to re-run with the same
 * deps (used for manual refresh buttons). */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: React.DependencyList,
): AsyncState<T> & { reload: () => void } {
  const [state, setState] = React.useState<AsyncState<T>>({
    data: null,
    error: null,
    loading: true,
  })
  const [tick, setTick] = React.useState(0)
  const fnRef = React.useRef(fn)
  fnRef.current = fn

  React.useEffect(() => {
    let cancelled = false
    setState((s) => ({ ...s, loading: true, error: null }))
    fnRef
      .current()
      .then((data) => {
        if (!cancelled) setState({ data, error: null, loading: false })
      })
      .catch((error: unknown) => {
        if (!cancelled) setState({ data: null, error, loading: false })
      })
    return () => {
      cancelled = true
    }
    // deps is the caller's explicit dependency array; fn is read via ref so
    // it doesn't need to be (and shouldn't be) in this list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])

  return { ...state, reload: () => setTick((t) => t + 1) }
}

/** Runs on demand via `run(...)` rather than automatically. For
 * user-triggered actions (submit a search, load a page, delete a row). */
export function useLazyAsync<Args extends unknown[], T>(
  fn: (...args: Args) => Promise<T>,
): AsyncState<T> & { run: (...args: Args) => Promise<T> } {
  const [state, setState] = React.useState<AsyncState<T>>({
    data: null,
    error: null,
    loading: false,
  })

  const run = React.useCallback(async (...args: Args) => {
    setState({ data: null, error: null, loading: true })
    try {
      const data = await fn(...args)
      setState({ data, error: null, loading: false })
      return data
    } catch (error) {
      setState({ data: null, error, loading: false })
      throw error
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { ...state, run }
}
