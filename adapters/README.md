# Framework adapters

Three lines to give any agent framework long-term memory:

```python
from sodamem import SodaMem
from adapters.langgraph import create_memory_tools     # or .crewai / .openai_agents

tools = create_memory_tools(SodaMem.open("./data/alice"), user_id="alice")
```

| Framework | Module | Extra | Verified against |
|---|---|---|---|
| LangGraph / LangChain | `adapters.langgraph` | `sodamem[langgraph]` | `langchain-core` 0.3 |
| CrewAI | `adapters.crewai` | `sodamem[crewai]` | `crewai` 1.15.10 |
| OpenAI Agents SDK | `adapters.openai_agents` | `sodamem[openai-agents]` | `openai-agents` 0.19.2 |
| Vercel AI SDK (TypeScript) | `sdk-ts` → `createMemoryTools` | `npm i sodamem` | — |

"Verified against" means `tests/test_adapters.py` builds the tools with that
package installed and calls one through **the framework's own invocation
path** — `Tool.invoke()`, `BaseTool.run()`, `FunctionTool.on_invoke_tool()` —
not through our function. Constructing a tool object proves nothing about
whether the framework can drive it; each of these three entry points has its
own argument-passing contract, and two of them were only discovered by
running the real package.

Each returns the same three tools:

- **`get_memory_context`** — a prompt-ready block: deduplicated, ranked,
  time-annotated, trimmed to a token budget, with citations for exactly the
  evidence the text contains. **Zero LLM calls.** This is the one most agents
  want, and the one mem0 and open-source Zep do not provide.
- **`search_memory`** — ranked raw records, when you want to post-process
  yourself.
- **`add_memory`** — store a conversation slice; facts are extracted and
  grounded to their source turns.

## Scope

```python
tools = create_memory_tools(mem, user_id="alice", agent_id="planner",
                            project_id="acme/api")
```

`agent_id` / `run_id` / `project_id` record **provenance** — which agent, run,
or repo contributed a fact. They narrow retrieval, they do not partition it: a
fact with no stamp stays visible under every narrowing, because it belongs to
the user. That is deliberate — it is what keeps "how did I solve this in the
other repo?" answerable, and it is why installing a project-scoped integration
never makes a user's existing memories disappear. **This is not an isolation
boundary** (see `server/routes/_scope.py`).

Scope is recorded once per ingest session, so it covers raw conversation turns
as well as extracted facts.

`user_id` is bound at construction and is deliberately **not** a tool
argument — an id the model can choose is an id the model can hallucinate.

## Adding a framework

Put the behavior in `adapters/_core.py` and keep the new file a shell. Four
copies of the same logic become four different behaviors; the shared
`MemoryTools` plus one wrapper per framework is what stops that. Import the
framework **inside** the factory function so `import adapters` stays free, and
raise an `ImportError` naming the extra that installs it.
