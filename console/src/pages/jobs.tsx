import * as React from 'react'
import { CheckCircle2, Clock, ListChecks, Loader2, XCircle } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { api } from '@/lib/api'
import { useConfig } from '@/lib/config'
import { useLazyAsync } from '@/lib/use-async'
import type { JobStatus } from '@/lib/types'

const POLL_INTERVAL_MS = 2000
const TERMINAL: JobStatus[] = ['succeeded', 'failed']

export function JobsPage() {
  const { apiKey, baseUrl } = useConfig()
  const cfg = React.useMemo(() => ({ apiKey, baseUrl }), [apiKey, baseUrl])

  const [jobId, setJobId] = React.useState('')
  const [submitted, setSubmitted] = React.useState(false)
  const [autoPoll, setAutoPoll] = React.useState(true)

  const job = useLazyAsync(api.getJob)

  const runFetch = React.useCallback(
    (id: string) => {
      if (!id.trim()) return
      setSubmitted(true)
      void job.run(cfg, id.trim())
    },
    // job.run has a stable identity from useLazyAsync
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cfg],
  )

  const status = job.data?.status
  const isTerminal = status ? TERMINAL.includes(status) : false

  React.useEffect(() => {
    if (!submitted || !autoPoll || isTerminal || job.loading) return
    const t = setTimeout(() => runFetch(jobId), POLL_INTERVAL_MS)
    return () => clearTimeout(t)
  }, [submitted, autoPoll, isTerminal, job.loading, jobId, runFetch])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Jobs</h1>
        <p className="text-sm text-muted-foreground">
          <code className="font-mono">GET /v1/jobs/{'{id}'}</code> — status for an async write
          (e.g. a memory ingest kicked off elsewhere via the API with{' '}
          <code className="font-mono">async_mode=true</code>). Job records persist across
          restarts; a job interrupted by one is reported as{' '}
          <code className="font-mono">failed</code> with a{' '}
          <code className="font-mono">server_restarted</code> error rather than left running
          forever.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Look up a job</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            className="grid gap-3 sm:grid-cols-[1fr_auto]"
            onSubmit={(e) => {
              e.preventDefault()
              runFetch(jobId)
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="job-id">job_id</Label>
              <Input
                id="job-id"
                value={jobId}
                onChange={(e) => setJobId(e.target.value)}
                placeholder="a1b2c3d4..."
                className="font-mono"
                required
              />
            </div>
            <div className="flex items-end gap-2">
              <Button type="submit" disabled={!jobId.trim() || job.loading}>
                Check status
              </Button>
              <Button
                type="button"
                variant={autoPoll ? 'secondary' : 'outline'}
                onClick={() => setAutoPoll((v) => !v)}
                title="Automatically refresh every 2s while pending/running"
              >
                {job.loading && autoPoll && !isTerminal ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Clock className="h-4 w-4" />
                )}
                Auto-refresh
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {!submitted && (
        <EmptyState
          icon={ListChecks}
          title="No job looked up yet"
          description="Paste a job_id above — you'll get one back as `job_id` from a 202 response to POST /v1/memories with async_mode=true."
        />
      )}

      {submitted && job.loading && !job.data && <Skeleton className="h-40 w-full" />}

      {submitted && job.error != null && <ErrorState error={job.error} onRetry={() => runFetch(jobId)} />}

      {submitted && job.error == null && job.data && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <CardTitle className="flex items-center gap-2 text-base">
              <StatusIcon status={job.data.status} />
              Job {job.data.job_id}
            </CardTitle>
            <StatusBadge status={job.data.status} />
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
              <dt className="text-muted-foreground">kind</dt>
              <dd className="font-mono">{job.data.kind}</dd>
              <dt className="text-muted-foreground">user_id</dt>
              <dd className="font-mono">{job.data.user_id}</dd>
              <dt className="text-muted-foreground">created_at</dt>
              <dd className="font-mono">{job.data.created_at}</dd>
              <dt className="text-muted-foreground">finished_at</dt>
              <dd className="font-mono">{job.data.finished_at ?? '—'}</dd>
            </dl>

            {job.data.error && (
              <div>
                <h3 className="mb-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                  Error
                </h3>
                <pre className="overflow-x-auto rounded-md bg-destructive/10 p-2 font-mono text-xs text-destructive">
                  {job.data.error}
                </pre>
              </div>
            )}

            {job.data.result && (
              <div>
                <h3 className="mb-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                  Result
                </h3>
                <pre className="overflow-x-auto rounded-md bg-muted p-2 font-mono text-xs">
                  {JSON.stringify(job.data.result, null, 2)}
                </pre>
              </div>
            )}

            {!isTerminal && autoPoll && (
              <p className="text-xs text-muted-foreground">
                Refreshing every {POLL_INTERVAL_MS / 1000}s while the job is {job.data.status}…
              </p>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}

function StatusIcon({ status }: { status: JobStatus }) {
  switch (status) {
    case 'succeeded':
      return <CheckCircle2 className="h-4 w-4 text-emerald-500" />
    case 'failed':
      return <XCircle className="h-4 w-4 text-destructive" />
    case 'running':
      return <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
    default:
      return <Clock className="h-4 w-4 text-muted-foreground" />
  }
}

function StatusBadge({ status }: { status: JobStatus }) {
  const variant = status === 'succeeded' ? 'default' : status === 'failed' ? 'destructive' : 'secondary'
  return <Badge variant={variant}>{status}</Badge>
}
