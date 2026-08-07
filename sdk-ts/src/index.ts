export * from "./client.js";
// Wire types are part of the public surface: `delete(id, scope, opts)` takes a
// DeleteOptions and returns a DeleteResult, so consumers must be able to name
// them. `export type *` keeps this erasable — nothing is added at runtime.
export type * from "./types.js";
