import { AlertTriangle } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'

/**
 * The one place that renders API failures. Always shows the machine-readable
 * `code` alongside the human message — per project policy, errors never get
 * flattened into a generic "Something went wrong."
 */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { code, message } = describeError(error)

  return (
    <Alert variant="destructive">
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle className="flex items-center gap-2">
        <span>Request failed</span>
        <code className="rounded bg-destructive/10 px-1.5 py-0.5 font-mono text-xs">{code}</code>
      </AlertTitle>
      <AlertDescription>
        <p>{message}</p>
        {onRetry && (
          <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
            Retry
          </Button>
        )}
      </AlertDescription>
    </Alert>
  )
}

export function describeError(error: unknown): { code: string; message: string } {
  if (error instanceof ApiError) {
    return { code: error.code, message: error.message }
  }
  if (error instanceof Error) {
    return { code: 'client_error', message: error.message }
  }
  return { code: 'unknown_error', message: String(error) }
}
