/**
 * Per-agent recall cache.
 *
 * Two jobs, both load-bearing:
 *
 * 1. It bridges the async `agent/pre-step` handler to the SYNCHRONOUS
 *    `systemPrompt.context` text provider — HTTP happens in the handler, the
 *    provider only reads.
 * 2. It makes recall fire once per turn instead of once per step. `pre-step`
 *    runs for every step of a tool loop and steps 2..n carry no new user
 *    message, so an entry for `(agentId, turn)` means "already handled".
 *
 * Keyed by agent id, so two agents never see each other's memory block.
 */

export interface RecallEntry {
  /** The turn this text was recalled for. */
  readonly turn: number;
  /** Sanitised, prompt-ready text. `''` means "no memory this turn". */
  readonly text: string;
}

export class RecallCache {
  private readonly entries = new Map<string, RecallEntry>();

  /** True when this agent's turn has already been handled (success or failure). */
  has(agentId: string, turn: number): boolean {
    const entry = this.entries.get(agentId);
    return entry !== undefined && entry.turn === turn;
  }

  set(agentId: string, turn: number, text: string): void {
    this.entries.set(agentId, { turn, text });
  }

  /** The text to contribute for this agent, or `''` when nothing is cached. */
  get(agentId: string | undefined): string {
    if (!agentId) return "";
    return this.entries.get(agentId)?.text ?? "";
  }

  delete(agentId: string): void {
    this.entries.delete(agentId);
  }

  clear(): void {
    this.entries.clear();
  }

  get size(): number {
    return this.entries.size;
  }
}
