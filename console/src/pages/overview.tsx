import * as React from 'react'
import { HardDrive, Key, Lock, LockOpen, RefreshCw, Server, Tag } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { ErrorState } from '@/components/error-state'
import { api } from '@/lib/api'
import { useConfig } from '@/lib/config'
import { useAsync } from '@/lib/use-async'

export function OverviewPage() {
  const { apiKey, setApiKey, baseUrl, setBaseUrl } = useConfig()
  const [apiKeyDraft, setApiKeyDraft] = React.useState(apiKey)
  const [baseUrlDraft, setBaseUrlDraft] = React.useState(baseUrl)

  const cfg = React.useMemo(() => ({ apiKey, baseUrl }), [apiKey, baseUrl])
  const health = useAsync(() => api.health(cfg), [cfg.apiKey, cfg.baseUrl])

  const dirty = apiKeyDraft !== apiKey || baseUrlDraft !== baseUrl

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
        <p className="text-sm text-muted-foreground">
          Connection status and credentials for this SodaMem deployment.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Key className="h-4 w-4" /> Connection settings
          </CardTitle>
          <CardDescription>
            Stored in this browser&apos;s <code className="font-mono">localStorage</code> only —
            never uploaded anywhere, never sent to any host other than the API base URL below.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="api-key">API key</Label>
            <Input
              id="api-key"
              type="password"
              autoComplete="off"
              placeholder="sk-..."
              value={apiKeyDraft}
              onChange={(e) => setApiKeyDraft(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Sent as <code className="font-mono">Authorization: Bearer &lt;key&gt;</code> on every
              request.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="base-url">API base URL</Label>
            <Input
              id="base-url"
              type="text"
              placeholder="Leave empty to use the same origin as this console"
              value={baseUrlDraft}
              onChange={(e) => setBaseUrlDraft(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Empty means requests go to <code className="font-mono">{window.location.origin}</code>{' '}
              (the normal case when the console is served by the sodamem API server itself).
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={!dirty}
              onClick={() => {
                setApiKey(apiKeyDraft)
                setBaseUrl(baseUrlDraft)
              }}
            >
              Save
            </Button>
            {dirty && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setApiKeyDraft(apiKey)
                  setBaseUrlDraft(baseUrl)
                }}
              >
                Discard changes
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <Server className="h-4 w-4" /> Server health
            </CardTitle>
            <CardDescription>
              <code className="font-mono">GET /health</code> — unauthenticated liveness probe.
            </CardDescription>
          </div>
          <Button size="icon" variant="ghost" onClick={health.reload} aria-label="Refresh">
            <RefreshCw className="h-4 w-4" />
          </Button>
        </CardHeader>
        <CardContent>
          {health.loading && (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          )}
          {!health.loading && health.error != null && (
            <ErrorState error={health.error} onRetry={health.reload} />
          )}
          {!health.loading && health.error == null && health.data && (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <StatTile icon={Tag} label="Status">
                <Badge variant={health.data.status === 'ok' ? 'default' : 'destructive'}>
                  {health.data.status}
                </Badge>
              </StatTile>
              <StatTile icon={HardDrive} label="Version">
                <span className="font-mono text-sm">{health.data.version}</span>
              </StatTile>
              <StatTile icon={HardDrive} label="Schema version">
                <span className="font-mono text-sm">{health.data.schema_version}</span>
              </StatTile>
              <StatTile icon={health.data.auth === 'enabled' ? Lock : LockOpen} label="Auth">
                <Badge variant={health.data.auth === 'enabled' ? 'default' : 'secondary'}>
                  {health.data.auth}
                </Badge>
              </StatTile>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function StatTile({
  icon: Icon,
  label,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-lg border p-3">
      <div className="mb-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      {children}
    </div>
  )
}
