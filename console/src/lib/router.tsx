import * as React from 'react'

/**
 * Deliberately not react-router: this console has six flat pages and no
 * nested/dynamic segments, so a real routing library buys nothing but a
 * bigger dependency tree (and, as of this writing, several open CVEs in its
 * SSR/RSC action-handling code paths that a pure static SPA never exercises
 * anyway). Hash-based navigation also means the console works correctly no
 * matter what path prefix it's served under (`/` in dev, `/console` when
 * FastAPI mounts `console/dist`) with zero basename bookkeeping.
 */

export type Route = 'overview' | 'memories' | 'search' | 'context' | 'jobs' | 'ops'

const ROUTES: Route[] = ['overview', 'memories', 'search', 'context', 'jobs', 'ops']
const DEFAULT_ROUTE: Route = 'overview'

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#\/?/, '')
  return (ROUTES as string[]).includes(raw) ? (raw as Route) : DEFAULT_ROUTE
}

export function navigate(route: Route): void {
  window.location.hash = `/${route}`
}

export function useRoute(): Route {
  const [route, setRoute] = React.useState<Route>(() => parseHash())

  React.useEffect(() => {
    const onHashChange = () => setRoute(parseHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  return route
}
