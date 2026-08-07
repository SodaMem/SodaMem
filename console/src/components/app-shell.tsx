import type { ReactNode } from 'react'
import { Database, Home, ListChecks, ScrollText, Search, SlidersHorizontal } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { type Route, navigate } from '@/lib/router'
import { ThemeToggle } from '@/components/theme-toggle'

const NAV_ITEMS: { route: Route; label: string; icon: LucideIcon }[] = [
  { route: 'overview', label: 'Overview', icon: Home },
  { route: 'memories', label: 'Memories', icon: Database },
  { route: 'search', label: 'Search', icon: Search },
  { route: 'context', label: 'Context', icon: ScrollText },
  { route: 'jobs', label: 'Jobs', icon: ListChecks },
  { route: 'ops', label: 'Ops', icon: SlidersHorizontal },
]

export function AppShell({ route, children }: { route: Route; children: ReactNode }) {
  return (
    <div className="flex min-h-svh w-full overflow-x-hidden bg-background text-foreground">
      <aside className="hidden w-56 shrink-0 flex-col border-r bg-card sm:flex">
        <div className="flex h-14 items-center gap-2 border-b px-4">
          <div className="flex h-6 w-6 items-center justify-center rounded bg-primary text-xs font-bold text-primary-foreground">
            S
          </div>
          <span className="font-semibold tracking-tight">SodaMem</span>
        </div>
        <nav className="flex-1 space-y-0.5 p-2">
          {NAV_ITEMS.map(({ route: r, label, icon: Icon }) => (
            <button
              key={r}
              type="button"
              onClick={() => navigate(r)}
              className={cn(
                'flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm font-medium transition-colors',
                route === r
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground',
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </button>
          ))}
        </nav>
        <div className="border-t p-3 text-xs text-muted-foreground">
          Read-mostly admin console
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b px-4 sm:hidden">
          <span className="font-semibold">SodaMem</span>
          <ThemeToggle />
        </header>
        <header className="hidden h-14 shrink-0 items-center justify-end border-b px-4 sm:flex">
          <ThemeToggle />
        </header>
        {/* Mobile nav (sidebar is hidden below sm) */}
        <nav className="flex gap-1 overflow-x-auto border-b bg-card px-2 py-1.5 sm:hidden">
          {NAV_ITEMS.map(({ route: r, label, icon: Icon }) => (
            <button
              key={r}
              type="button"
              onClick={() => navigate(r)}
              className={cn(
                'flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium',
                route === r
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-muted',
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </nav>
        <main className="min-w-0 flex-1 overflow-y-auto overflow-x-hidden p-4 sm:p-6">
          <div className="mx-auto w-full max-w-5xl">{children}</div>
        </main>
      </div>
    </div>
  )
}
