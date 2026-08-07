import * as React from 'react'
import { Activity, Copy, KeyRound, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { api } from '@/lib/api'
import { useConfig } from '@/lib/config'
import { useAsync } from '@/lib/use-async'
import type { ApiKeySummary, RequestLogEntry } from '@/lib/types'

const REQUEST_PAGE_SIZE = 50

/**
 * Ops: what is this box configured with, who is calling it, and what has it
 * been doing. Single-tenant self-hosting still needs those answers; today
 * getting them means shell access to the container.
 *
 * One deliberate omission: a minted key's plaintext is rendered once and is
 * NOT written to localStorage, sessionStorage, or the config store. The
 * console already keeps the operator's own API key in localStorage (a known,
 * documented risk pending a CSP/XSS review) — this page must not widen that
 * surface by persisting every key it mints.
 */
export function OpsPage() {
  const { apiKey, baseUrl } = useConfig()
  const cfg = React.useMemo(() => ({ apiKey, baseUrl }), [apiKey, baseUrl])

  const config = useAsync(() => api.getAdminConfig(cfg), [cfg])
  const stats = useAsync(() => api.getStats(cfg), [cfg])
  const keys = useAsync(() => api.listApiKeys(cfg), [cfg])
  const requests = useAsync(() => api.listRequests(cfg, { limit: REQUEST_PAGE_SIZE }), [cfg])

  const [newKeyName, setNewKeyName] = React.useState('')
  const [minted, setMinted] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  // A shadcn Dialog, not window.confirm: the rest of the console confirms
  // destructive actions this way (see memories.tsx's DeleteConfirmDialog), and
  // a native modal blocks the whole renderer — which is exactly how it wedged
  // during the 0729 browser pass.
  const [pendingRevoke, setPendingRevoke] = React.useState<ApiKeySummary | null>(null)

  const reloadAll = React.useCallback(() => {
    config.reload()
    stats.reload()
    keys.reload()
    requests.reload()
    // reload identities are stable per useAsync
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function createKey(e: React.FormEvent) {
    e.preventDefault()
    const name = newKeyName.trim()
    if (!name) return
    setBusy(true)
    try {
      const created = await api.createApiKey(cfg, name)
      setMinted(created.api_key)
      setNewKeyName('')
      keys.reload()
      config.reload()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not create the key')
    } finally {
      setBusy(false)
    }
  }

  async function revokeKey(key: ApiKeySummary) {
    setBusy(true)
    try {
      await api.revokeApiKey(cfg, key.id)
      toast.success(`Revoked ${key.name}`)
      setPendingRevoke(null)
      keys.reload()
      config.reload()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not revoke the key')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Ops</h1>
          <p className="text-sm text-muted-foreground">
            <code className="font-mono">/v1/admin/*</code> — effective configuration, API keys,
            recent requests and disk usage for this deployment.
          </p>
        </div>
        <Button variant="outline" onClick={reloadAll} disabled={busy}>
          <RefreshCw className="h-4 w-4" />
          Refresh
        </Button>
      </div>

      {/* --- configuration --- */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheck className="h-4 w-4" />
            Configuration
          </CardTitle>
        </CardHeader>
        <CardContent>
          {config.loading && !config.data && <Skeleton className="h-40 w-full" />}
          {config.error != null && <ErrorState error={config.error} onRetry={config.reload} />}
          {config.data && config.data.lock_contention_count > 0 && (
            // The one thing on this page that no other signal will tell you:
            // the container stays healthy and `/health` stays 200 while extra
            // workers crash-loop behind it.
            <Alert variant="destructive" className="mb-4">
              <AlertTitle>More than one process is trying to serve this data root</AlertTitle>
              <AlertDescription>
                {config.data.lock_contention_count} start(s) refused, most recently{' '}
                {config.data.lock_contention_last
                  ? formatTime(config.data.lock_contention_last)
                  : 'unknown'}
                . Your data is safe — only one writer ever holds the lock — but the rejected
                processes are in a crash-restart loop, and neither the container status nor{' '}
                <code className="font-mono">/health</code> can see it. Run a single worker
                (<code className="font-mono">--workers 1</code>), or give each instance its own{' '}
                <code className="font-mono">SODAMEM_DATA_ROOT</code>.
              </AlertDescription>
            </Alert>
          )}
          {config.data && (
            <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
              <Row label="data_root" value={config.data.data_root} mono />
              <Row label="auth" value={config.data.auth} />
              <Row
                label="bootstrap key"
                value={config.data.api_key_set ? 'configured' : 'not set'}
              />
              <Row label="named keys (active)" value={String(config.data.named_keys_active)} />
              <Row label="llm_provider" value={config.data.llm_provider} mono />
              <Row label="llm_model" value={config.data.llm_model ?? '—'} mono />
              <Row
                label="llm key"
                value={config.data.llm_api_key_set ? 'configured' : 'not set'}
              />
              <Row label="store_cache_max" value={String(config.data.store_cache_max)} />
              <Row label="request_log_max" value={String(config.data.request_log_max)} />
              <Row label="job_retention_max" value={String(config.data.job_retention_max)} />
              <Row
                label="cors_origins"
                value={config.data.cors_origins.length ? config.data.cors_origins.join(', ') : '—'}
                mono
              />
              <Row label="workers" value={`${config.data.workers} (correctness constraint)`} />
            </dl>
          )}
          <p className="mt-4 text-xs text-muted-foreground">
            Secrets are reported as configured / not set, never masked: a mask that preserves
            length leaks more than it appears to.
          </p>
        </CardContent>
      </Card>

      {/* --- stats --- */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Activity className="h-4 w-4" />
            Storage &amp; workload
          </CardTitle>
        </CardHeader>
        <CardContent>
          {stats.loading && !stats.data && <Skeleton className="h-32 w-full" />}
          {stats.error != null && <ErrorState error={stats.error} onRetry={stats.reload} />}
          {stats.data && (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-4">
                <Stat label="Users" value={String(stats.data.users)} />
                <Stat label="Stores" value={formatBytes(stats.data.stores_bytes)} />
                <Stat label="Control DB" value={formatBytes(stats.data.control_db_bytes)} />
                <Stat label="Requests logged" value={String(stats.data.requests_logged)} />
              </div>

              {Object.keys(stats.data.jobs_by_status).length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {Object.entries(stats.data.jobs_by_status).map(([status, n]) => (
                    <Badge key={status} variant="secondary">
                      {status}: {n}
                    </Badge>
                  ))}
                </div>
              )}

              {stats.data.largest_stores.length > 0 && (
                <div>
                  <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                    Largest stores
                  </h3>
                  <div className="space-y-1">
                    {stats.data.largest_stores.map((s) => (
                      <div key={s.user_id} className="flex justify-between text-sm">
                        <span className="truncate font-mono">{s.user_id}</span>
                        <span className="text-muted-foreground">{formatBytes(s.bytes)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* --- api keys --- */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRound className="h-4 w-4" />
            API keys
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {minted && (
            <Alert>
              <AlertTitle>Copy this key now</AlertTitle>
              <AlertDescription className="space-y-2">
                <p>
                  This is the only time the plaintext is shown. It is stored as a digest on the
                  server and cannot be retrieved again.
                </p>
                <div className="flex w-full items-center gap-2">
                  <code className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1 font-mono text-xs">
                    {minted}
                  </code>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      void navigator.clipboard.writeText(minted)
                      toast.success('Key copied to clipboard')
                    }}
                  >
                    <Copy className="h-3.5 w-3.5" />
                    Copy
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setMinted(null)}>
                    Dismiss
                  </Button>
                </div>
              </AlertDescription>
            </Alert>
          )}

          <form className="grid gap-3 sm:grid-cols-[1fr_auto]" onSubmit={createKey}>
            <div className="space-y-1.5">
              <Label htmlFor="key-name">New key name</Label>
              <Input
                id="key-name"
                value={newKeyName}
                onChange={(e) => setNewKeyName(e.target.value)}
                placeholder="ci-pipeline"
                maxLength={64}
                required
              />
            </div>
            <div className="flex items-end">
              <Button type="submit" disabled={busy || !newKeyName.trim()}>
                Create key
              </Button>
            </div>
          </form>

          {keys.loading && !keys.data && <Skeleton className="h-24 w-full" />}
          {keys.error != null && <ErrorState error={keys.error} onRetry={keys.reload} />}
          {keys.data && keys.data.total === 0 && (
            <EmptyState
              icon={KeyRound}
              title="No named keys yet"
              description="The bootstrap key from SODAMEM_API_KEY still works. Named keys add attribution — each request records which key made it."
            />
          )}
          {keys.data && keys.data.total > 0 && (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Prefix</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead>Last used</TableHead>
                    <TableHead className="text-right">Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {keys.data.keys.map((k) => (
                    <TableRow key={k.id}>
                      <TableCell className="font-medium">{k.name}</TableCell>
                      <TableCell className="font-mono text-xs">{k.prefix}…</TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatTime(k.created_at)}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {k.last_used_at ? formatTime(k.last_used_at) : 'never'}
                      </TableCell>
                      <TableCell className="text-right">
                        {k.revoked ? (
                          <Badge variant="outline">revoked</Badge>
                        ) : (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy}
                            onClick={() => setPendingRevoke(k)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                            Revoke
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* --- request log --- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent requests</CardTitle>
        </CardHeader>
        <CardContent>
          {requests.loading && !requests.data && <Skeleton className="h-40 w-full" />}
          {requests.error != null && (
            <ErrorState error={requests.error} onRetry={requests.reload} />
          )}
          {requests.data && requests.data.requests.length === 0 && (
            <EmptyState
              icon={Activity}
              title="Nothing logged"
              description="Either no requests have been served yet, or SODAMEM_REQUEST_LOG_MAX is set to 0."
            />
          )}
          {requests.data && requests.data.requests.length > 0 && (
            <>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>When</TableHead>
                      <TableHead>Route</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Latency</TableHead>
                      <TableHead>Caller</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {requests.data.requests.map((r) => (
                      <TableRow key={r.request_id + r.created_at}>
                        <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
                          {formatTime(r.created_at)}
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {r.method} {r.route}
                        </TableCell>
                        <TableCell>
                          <StatusBadge entry={r} />
                        </TableCell>
                        <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
                          {r.latency_ms.toFixed(1)} ms
                        </TableCell>
                        <TableCell className="text-xs">{r.key_name ?? '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <p className="mt-3 text-xs text-muted-foreground">
                Showing {requests.data.requests.length} of {requests.data.total} retained. This is
                a rolling window capped at <code className="font-mono">request_log_max</code> rows,
                not an archive.
              </p>
            </>
          )}
        </CardContent>
      </Card>

      <Dialog open={pendingRevoke != null} onOpenChange={(open) => !open && setPendingRevoke(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Revoke this key?</DialogTitle>
            <DialogDescription>
              Calls using it start failing immediately. The row is kept, not deleted — a revoked
              key that vanishes takes its request history's only explanation with it. This cannot
              be undone; issue a new key instead.
            </DialogDescription>
          </DialogHeader>
          {pendingRevoke && (
            <div className="rounded-md border bg-muted/50 p-3 text-sm">
              <p className="font-medium">{pendingRevoke.name}</p>
              <p className="font-mono text-xs text-muted-foreground">{pendingRevoke.prefix}…</p>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingRevoke(null)} disabled={busy}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => pendingRevoke && void revokeKey(pendingRevoke)}
            >
              {busy ? 'Revoking…' : 'Revoke key'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4 border-b py-1 last:border-0">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className={`min-w-0 truncate text-right ${mono ? 'font-mono text-xs' : ''}`}>{value}</dd>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-0.5 text-lg font-semibold tabular-nums">{value}</div>
    </div>
  )
}

function StatusBadge({ entry }: { entry: RequestLogEntry }) {
  const variant =
    entry.status_code >= 500
      ? 'destructive'
      : entry.status_code >= 400
        ? 'outline'
        : 'secondary'
  return <Badge variant={variant}>{entry.status_code}</Badge>
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = n / 1024
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i += 1
  }
  return `${value.toFixed(1)} ${units[i]}`
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}
