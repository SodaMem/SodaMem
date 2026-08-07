import { AppShell } from '@/components/app-shell'
import { ThemeProvider } from '@/components/theme-provider'
import { Toaster } from '@/components/ui/sonner'
import { ConfigProvider } from '@/lib/config'
import { useRoute } from '@/lib/router'
import { ContextPage } from '@/pages/context'
import { JobsPage } from '@/pages/jobs'
import { MemoriesPage } from '@/pages/memories'
import { OpsPage } from '@/pages/ops'
import { OverviewPage } from '@/pages/overview'
import { SearchPage } from '@/pages/search'

function RoutedPage() {
  const route = useRoute()
  switch (route) {
    case 'overview':
      return <OverviewPage />
    case 'memories':
      return <MemoriesPage />
    case 'search':
      return <SearchPage />
    case 'context':
      return <ContextPage />
    case 'jobs':
      return <JobsPage />
    case 'ops':
      return <OpsPage />
  }
}

function AppContent() {
  const route = useRoute()
  return (
    <AppShell route={route}>
      <RoutedPage />
    </AppShell>
  )
}

export default function App() {
  return (
    <ThemeProvider>
      <ConfigProvider>
        <AppContent />
        <Toaster position="bottom-right" />
      </ConfigProvider>
    </ThemeProvider>
  )
}
