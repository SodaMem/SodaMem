import * as React from 'react'
import { ChevronLeft, ChevronRight, Database, Search as SearchIcon, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
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
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
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
import { ErrorState, describeError } from '@/components/error-state'
import { api } from '@/lib/api'
import { useConfig } from '@/lib/config'
import { useLazyAsync } from '@/lib/use-async'
import type { Memory } from '@/lib/types'

const LIMIT_OPTIONS = [10, 20, 50, 100]

export function MemoriesPage() {
  const { apiKey, baseUrl } = useConfig()
  const cfg = React.useMemo(() => ({ apiKey, baseUrl }), [apiKey, baseUrl])

  const [userId, setUserId] = React.useState('')
  const [agentId, setAgentId] = React.useState('')
  const [runId, setRunId] = React.useState('')
  const [offset, setOffset] = React.useState(0)
  const [limit, setLimit] = React.useState(20)
  const [queried, setQueried] = React.useState(false)

  const [selected, setSelected] = React.useState<Memory | null>(null)
  const [pendingDelete, setPendingDelete] = React.useState<Memory | null>(null)

  const list = useLazyAsync(api.listMemories)

  const load = React.useCallback(
    (nextOffset: number) => {
      if (!userId.trim()) return
      setOffset(nextOffset)
      setQueried(true)
      void list.run(cfg, {
        user_id: userId.trim(),
        agent_id: agentId.trim() || undefined,
        run_id: runId.trim() || undefined,
        offset: nextOffset,
        limit,
      })
    },
    // list.run is a stable useCallback identity from useLazyAsync
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cfg, userId, agentId, runId, limit],
  )

  const handleDeleted = (id: string) => {
    setSelected((s) => (s?.id === id ? null : s))
    setPendingDelete(null)
    load(offset)
  }

  const total = list.data?.total ?? 0
  const hasPrev = offset > 0
  const hasNext = offset + limit < total

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Memories</h1>
        <p className="text-sm text-muted-foreground">
          Browse stored memories for a user. Scoped reads only — every query requires a
          <code className="mx-1 font-mono">user_id</code>.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Scope</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            className="grid gap-3 sm:grid-cols-[1fr_1fr_1fr_auto]"
            onSubmit={(e) => {
              e.preventDefault()
              load(0)
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="mem-user-id">user_id (required)</Label>
              <Input
                id="mem-user-id"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                placeholder="user_123"
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mem-agent-id">agent_id</Label>
              <Input
                id="mem-agent-id"
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                placeholder="optional"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mem-run-id">run_id</Label>
              <Input
                id="mem-run-id"
                value={runId}
                onChange={(e) => setRunId(e.target.value)}
                placeholder="optional"
              />
            </div>
            <div className="flex items-end">
              <Button type="submit" disabled={!userId.trim() || list.loading} className="w-full sm:w-auto">
                <SearchIcon className="h-4 w-4" />
                Load
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {!queried && (
        <EmptyState
          icon={Database}
          title="No query yet"
          description="Enter a user_id above and click Load to list their memories."
        />
      )}

      {queried && list.loading && (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      )}

      {queried && !list.loading && list.error != null && (
        <ErrorState error={list.error} onRetry={() => load(offset)} />
      )}

      {queried && !list.loading && list.error == null && list.data && (
        <Card>
          <CardContent className="p-0">
            {list.data.memories.length === 0 ? (
              <EmptyState
                icon={Database}
                title="No memories found"
                description="This scope has no memories, or they've all been deleted."
              />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Content</TableHead>
                    <TableHead>Kind</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Occurred at</TableHead>
                    <TableHead>Confidence</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {list.data.memories.map((m) => (
                    <TableRow
                      key={m.id}
                      className="cursor-pointer"
                      onClick={() => setSelected(m)}
                    >
                      <TableCell className="max-w-xs truncate whitespace-nowrap">
                        {m.content}
                      </TableCell>
                      <TableCell>
                        {m.kind ? <Badge variant="outline">{m.kind}</Badge> : '—'}
                      </TableCell>
                      <TableCell>
                        {m.status ? <Badge variant="secondary">{m.status}</Badge> : '—'}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {m.occurred_at ?? '—'}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {m.confidence != null ? m.confidence.toFixed(2) : '—'}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label="Delete memory"
                          onClick={(e) => {
                            e.stopPropagation()
                            setPendingDelete(m)
                          }}
                        >
                          <Trash2 className="h-3.5 w-3.5 text-destructive" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {queried && !list.loading && list.error == null && list.data && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span>
              {total === 0
                ? '0 results'
                : `${offset + 1}–${Math.min(offset + limit, total)} of ${total}`}
            </span>
            <Select value={String(limit)} onValueChange={(v) => setLimit(Number(v))}>
              <SelectTrigger size="sm" className="w-24">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LIMIT_OPTIONS.map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n} / page
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!hasPrev || list.loading}
              onClick={() => load(Math.max(0, offset - limit))}
            >
              <ChevronLeft className="h-4 w-4" /> Prev
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!hasNext || list.loading}
              onClick={() => load(offset + limit)}
            >
              Next <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}

      <MemoryDetailDialog
        memory={selected}
        onClose={() => setSelected(null)}
        onDeleteRequest={(m) => setPendingDelete(m)}
      />
      <DeleteConfirmDialog
        key={pendingDelete?.id ?? 'none'}
        memory={pendingDelete}
        cfg={cfg}
        onCancel={() => setPendingDelete(null)}
        onDeleted={handleDeleted}
      />
    </div>
  )
}

function MemoryDetailDialog({
  memory,
  onClose,
  onDeleteRequest,
}: {
  memory: Memory | null
  onClose: () => void
  onDeleteRequest: (m: Memory) => void
}) {
  return (
    <Dialog open={memory != null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-hidden sm:max-w-2xl">
        {memory && (
          <>
            <DialogHeader>
              <DialogTitle className="font-mono text-sm">{memory.id}</DialogTitle>
              <DialogDescription>user_id: {memory.user_id}</DialogDescription>
            </DialogHeader>
            <ScrollArea className="max-h-[60vh] pr-3">
              <div className="space-y-4 text-sm">
                <Section label="Content">
                  <p className="whitespace-pre-wrap">{memory.content}</p>
                </Section>

                <Section label="Classification">
                  <div className="flex flex-wrap gap-2">
                    {memory.kind && <Badge variant="outline">kind: {memory.kind}</Badge>}
                    {memory.status && <Badge variant="secondary">status: {memory.status}</Badge>}
                    {memory.confidence != null && (
                      <Badge variant="outline">confidence: {memory.confidence.toFixed(3)}</Badge>
                    )}
                  </div>
                </Section>

                <Section label="Time fields">
                  <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-xs">
                    <dt className="text-muted-foreground">occurred_at</dt>
                    <dd>{memory.occurred_at ?? '—'}</dd>
                    <dt className="text-muted-foreground">valid_from</dt>
                    <dd>{memory.valid_from ?? '—'}</dd>
                    <dt className="text-muted-foreground">valid_until</dt>
                    <dd>{memory.valid_until ?? '—'}</dd>
                    <dt className="text-muted-foreground">session_id</dt>
                    <dd>{memory.session_id ?? '—'}</dd>
                  </dl>
                </Section>

                <Section label="Metadata (evidence chain, spans, source refs — raw)">
                  {Object.keys(memory.metadata ?? {}).length === 0 ? (
                    <p className="text-muted-foreground">Empty.</p>
                  ) : (
                    <pre className="overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs">
                      {JSON.stringify(memory.metadata, null, 2)}
                    </pre>
                  )}
                </Section>
              </div>
            </ScrollArea>
            <DialogFooter>
              <Button variant="destructive" onClick={() => onDeleteRequest(memory)}>
                <Trash2 className="h-4 w-4" />
                Delete
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {label}
      </h3>
      {children}
    </div>
  )
}

function DeleteConfirmDialog({
  memory,
  cfg,
  onCancel,
  onDeleted,
}: {
  memory: Memory | null
  cfg: { apiKey: string; baseUrl: string }
  onCancel: () => void
  onDeleted: (id: string) => void
}) {
  // Keyed by memory.id in the parent, so this component remounts (and
  // useLazyAsync's state resets) fresh for each memory rather than needing
  // manual reset logic here.
  const del = useLazyAsync(api.deleteMemory)

  if (!memory) return null

  return (
    <Dialog open onOpenChange={(open) => !open && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete this memory?</DialogTitle>
          <DialogDescription>
            It stops appearing in search, context, and this list. The underlying record is
            kept as a tombstone so answers that already cited it keep their provenance.
            Permanent erasure is an operator-only action and isn&apos;t available here.
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-md border bg-muted/50 p-3 text-sm">
          <p className="mb-1 font-mono text-xs text-muted-foreground">{memory.id}</p>
          <p className="line-clamp-3">{memory.content}</p>
        </div>
        {del.error != null && <ErrorState error={del.error} />}
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={del.loading}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={del.loading}
            onClick={async () => {
              try {
                const result = await del.run(cfg, memory.id, memory.user_id)
                toast.success(`Deleted ${memory.id}`, {
                  description: result.already_deleted
                    ? 'It had already been deleted.'
                    : 'Removed from search and context; the record is retained.',
                })
                onDeleted(memory.id)
              } catch (err) {
                toast.error('Delete failed', { description: describeError(err).message })
              }
            }}
          >
            {del.loading ? 'Deleting…' : 'Delete'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
