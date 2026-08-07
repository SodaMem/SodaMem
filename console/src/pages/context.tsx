import * as React from 'react'
import { AlertTriangle, Check, Copy, ScrollText, Zap } from 'lucide-react'
import { toast } from 'sonner'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Slider } from '@/components/ui/slider'
import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { api } from '@/lib/api'
import { useConfig } from '@/lib/config'
import { useLazyAsync } from '@/lib/use-async'

export function ContextPage() {
  const { apiKey, baseUrl } = useConfig()
  const cfg = React.useMemo(() => ({ apiKey, baseUrl }), [apiKey, baseUrl])

  const [userId, setUserId] = React.useState('')
  const [query, setQuery] = React.useState('')
  const [tokenBudget, setTokenBudget] = React.useState(2000)
  const [submitted, setSubmitted] = React.useState(false)
  const [copied, setCopied] = React.useState(false)

  const ctx = useLazyAsync(api.getContext)

  const runFetch = () => {
    if (!userId.trim() || !query.trim()) return
    setSubmitted(true)
    void ctx.run(cfg, { user_id: userId.trim(), query: query.trim(), token_budget: tokenBudget })
  }

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    runFetch()
  }

  const copyText = async () => {
    if (!ctx.data) return
    try {
      await navigator.clipboard.writeText(ctx.data.text)
      setCopied(true)
      toast.success('Copied to clipboard')
      setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error('Clipboard write failed', {
        description: 'Your browser blocked programmatic clipboard access.',
      })
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Context</h1>
        <p className="text-sm text-muted-foreground">
          <code className="font-mono">GET /v1/context</code> — a prompt-ready evidence block.
        </p>
      </div>

      <Alert>
        <Zap className="h-4 w-4" />
        <AlertTitle>Zero LLM calls on this path</AlertTitle>
        <AlertDescription>
          Context assembly is pure retrieval + formatting — no model inference happens between
          your query and the text below. That&apos;s what makes it fast and deterministic enough
          to sit in a hot path.
        </AlertDescription>
      </Alert>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Query</CardTitle>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={onSubmit}>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="ctx-user-id">user_id</Label>
                <Input
                  id="ctx-user-id"
                  value={userId}
                  onChange={(e) => setUserId(e.target.value)}
                  placeholder="user_123"
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ctx-query">query</Label>
                <Input
                  id="ctx-query"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="What should I know before replying?"
                  required
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="ctx-budget">token_budget</Label>
                <span className="font-mono text-xs text-muted-foreground">{tokenBudget}</span>
              </div>
              <Slider
                id="ctx-budget"
                min={100}
                max={32000}
                step={100}
                value={[tokenBudget]}
                onValueChange={([v]) => setTokenBudget(v)}
              />
            </div>
            <Button type="submit" disabled={!userId.trim() || !query.trim() || ctx.loading}>
              <ScrollText className="h-4 w-4" />
              Fetch context
            </Button>
          </form>
        </CardContent>
      </Card>

      {!submitted && (
        <EmptyState
          icon={ScrollText}
          title="No context fetched yet"
          description="Enter a user_id and query above to assemble a prompt-ready evidence block."
        />
      )}

      {submitted && ctx.loading && <Skeleton className="h-64 w-full" />}

      {submitted && !ctx.loading && ctx.error != null && (
        <ErrorState error={ctx.error} onRetry={runFetch} />
      )}

      {submitted && !ctx.loading && ctx.error == null && ctx.data && (
        <div className="space-y-4">
          {ctx.data.degraded.length > 0 && (
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Retrieval degraded</AlertTitle>
              <AlertDescription>
                <pre className="mt-1 overflow-x-auto rounded-md bg-destructive/10 p-2 font-mono text-xs">
                  {JSON.stringify(ctx.data.degraded, null, 2)}
                </pre>
              </AlertDescription>
            </Alert>
          )}

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0">
              <CardTitle className="text-base">Prompt-ready text</CardTitle>
              <Button variant="outline" size="sm" onClick={copyText}>
                {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                {copied ? 'Copied' : 'Copy'}
              </Button>
            </CardHeader>
            <CardContent>
              {ctx.data.text ? (
                <pre className="max-h-96 overflow-auto rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap">
                  {ctx.data.text}
                </pre>
              ) : (
                <p className="text-sm text-muted-foreground">Empty — no evidence in budget.</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Citations{' '}
                <span className="text-sm font-normal text-muted-foreground">
                  ({ctx.data.citations.length})
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {ctx.data.citations.length === 0 ? (
                <p className="text-sm text-muted-foreground">No citations.</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {ctx.data.citations.map((c, i) => (
                    <Badge key={`${c}-${i}`} variant="outline" className="font-mono">
                      {c}
                    </Badge>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
