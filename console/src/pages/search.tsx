import * as React from 'react'
import { AlertTriangle, Search as SearchIcon, SearchX } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
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

export function SearchPage() {
  const { apiKey, baseUrl } = useConfig()
  const cfg = React.useMemo(() => ({ apiKey, baseUrl }), [apiKey, baseUrl])

  const [userId, setUserId] = React.useState('')
  const [query, setQuery] = React.useState('')
  const [topK, setTopK] = React.useState(10)
  const [submitted, setSubmitted] = React.useState(false)

  const search = useLazyAsync(api.search)

  const runSearch = () => {
    if (!userId.trim() || !query.trim()) return
    setSubmitted(true)
    void search.run(cfg, { user_id: userId.trim(), query: query.trim(), top_k: topK })
  }

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    runSearch()
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Search</h1>
        <p className="text-sm text-muted-foreground">
          <code className="font-mono">POST /v1/search</code> — ranked memory hits for a query.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Query</CardTitle>
        </CardHeader>
        <CardContent>
          <form className="grid gap-3 sm:grid-cols-[1fr_1fr_auto_auto]" onSubmit={onSubmit}>
            <div className="space-y-1.5">
              <Label htmlFor="search-user-id">user_id</Label>
              <Input
                id="search-user-id"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                placeholder="user_123"
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="search-query">query</Label>
              <Input
                id="search-query"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="What does the user prefer for breakfast?"
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="search-top-k">top_k</Label>
              <Input
                id="search-top-k"
                type="number"
                min={1}
                max={100}
                value={topK}
                onChange={(e) => setTopK(Number(e.target.value) || 1)}
                className="w-24"
              />
            </div>
            <div className="flex items-end">
              <Button type="submit" disabled={!userId.trim() || !query.trim() || search.loading}>
                <SearchIcon className="h-4 w-4" />
                Search
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {!submitted && (
        <EmptyState
          icon={SearchIcon}
          title="No search yet"
          description="Enter a user_id and query above to search that user's memories."
        />
      )}

      {submitted && search.loading && (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      )}

      {submitted && !search.loading && search.error != null && (
        <ErrorState error={search.error} onRetry={runSearch} />
      )}

      {submitted && !search.loading && search.error == null && search.data && (
        <div className="space-y-4">
          {search.data.degraded.length > 0 && (
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Retrieval degraded</AlertTitle>
              <AlertDescription>
                <p className="mb-2">
                  A retrieval route fell back during this search. Results may be less relevant
                  than usual.
                </p>
                <pre className="overflow-x-auto rounded-md bg-destructive/10 p-2 font-mono text-xs">
                  {JSON.stringify(search.data.degraded, null, 2)}
                </pre>
              </AlertDescription>
            </Alert>
          )}

          {search.data.hits.length === 0 ? (
            <EmptyState
              icon={SearchX}
              title="No hits"
              description="Nothing in this scope matched the query."
            />
          ) : (
            <div className="space-y-3">
              {search.data.hits.map((hit) => (
                <Card key={hit.id}>
                  <CardContent className="space-y-2 pt-4">
                    <div className="flex items-start justify-between gap-3">
                      <p className="text-sm">{hit.content}</p>
                      {hit.score != null && (
                        <Badge variant="outline" className="shrink-0 font-mono">
                          {hit.score.toFixed(3)}
                        </Badge>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-muted-foreground">
                      <span>{hit.id}</span>
                      {hit.occurred_at && <span>occurred_at: {hit.occurred_at}</span>}
                      {hit.session_id && <span>session: {hit.session_id}</span>}
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
