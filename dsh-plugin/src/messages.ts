/**
 * Projection from dsh content blocks to the flat `{ role, content }` messages
 * the SodaMem REST API speaks.
 */
import type { ContentBlock } from "@deepseek-ai/dsh-llm";

/**
 * Concatenate the text of every `TextBlock` in `content`.
 *
 * `ContentBlock` is a merge-extensible union (`text | reasoning | image |
 * tool-call | tool-result`, plus whatever plugins add). Only `text` is model-
 * visible prose worth remembering — reasoning is not user-facing, and tool
 * traffic is machine chatter that would drown the extractor.
 */
export function renderTextBlocks(content: readonly ContentBlock[] | undefined): string {
  if (!content) return "";
  const parts: string[] = [];
  for (const block of content) {
    if (block && block.type === "text" && typeof block.text === "string") {
      const text = block.text.trim();
      if (text) parts.push(text);
    }
  }
  return parts.join("\n");
}

/**
 * Neutralise `{{` so the harness's strict `{{variable}}` interpolation cannot
 * fail on remembered text.
 *
 * Prompt contexts go through the same interpolation as sections, and an
 * unresolvable reference makes the whole assembly fail — i.e. a stored memory
 * that happens to contain `{{foo}}` would break the user's turn. Inserting a
 * space after every `{` that is followed by another `{` leaves no `{{` token
 * behind, even in runs of three or more braces.
 */
export function sanitizeContextText(text: string): string {
  return text.replace(/\{(?=\{)/g, "{ ");
}
