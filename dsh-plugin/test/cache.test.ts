import { describe, expect, it } from "vitest";
import { RecallCache } from "../src/index.js";

describe("RecallCache", () => {
  it("reports a turn as handled only for the agent and turn it was set for", () => {
    const cache = new RecallCache();
    cache.set("agent-a", 3, "remembered");

    expect(cache.has("agent-a", 3)).toBe(true);
    expect(cache.has("agent-a", 4)).toBe(false);
    expect(cache.has("agent-b", 3)).toBe(false);
  });

  it("keeps agents apart", () => {
    const cache = new RecallCache();
    cache.set("agent-a", 1, "alpha");
    cache.set("agent-b", 1, "beta");

    expect(cache.get("agent-a")).toBe("alpha");
    expect(cache.get("agent-b")).toBe("beta");
    expect(cache.get("agent-c")).toBe("");
    expect(cache.get(undefined)).toBe("");
  });

  it("replaces the previous turn's entry for the same agent", () => {
    const cache = new RecallCache();
    cache.set("agent-a", 1, "old");
    cache.set("agent-a", 2, "new");

    expect(cache.get("agent-a")).toBe("new");
    expect(cache.has("agent-a", 1)).toBe(false);
    expect(cache.size).toBe(1);
  });

  it("evicts one agent without touching the others, and clears everything", () => {
    const cache = new RecallCache();
    cache.set("agent-a", 1, "alpha");
    cache.set("agent-b", 1, "beta");

    cache.delete("agent-a");
    expect(cache.get("agent-a")).toBe("");
    expect(cache.get("agent-b")).toBe("beta");

    cache.clear();
    expect(cache.size).toBe(0);
  });
});
