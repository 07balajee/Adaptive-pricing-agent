# Adaptive Pricing Agent — Agentic AI Study Guide

This guide turns the pricing agent into a structured learning path for MCP, A2A, LangGraph,
and the surrounding production-AI-engineer toolkit. Each phase builds directly on your
existing code (`agents/`, `adaptors/`, `main.py`) and ends with something you can run and
verify before moving on. Don't skip ahead — each phase exists because the previous one hits
a real limitation.

**How to use this file:** work top to bottom. Each phase has Concepts (read first),
Tasks (do in order), a Validation check (don't move on until it passes), and Reflection
questions (answer in your own words — if you can't, re-read the concepts).

---

## Phase 0 — Where you're starting from

Current architecture (all in-process, no framework):

```
main.py → agents/pricing_strategist.py → agents/signal_collector.py (weather)
                                        → adaptors/vendor_adaptors.py (rule-based price)
                                        → Groq API (LLM reasoning) with local mock fallback
```

Everything is a direct Python function call. The rest of this guide replaces those function
calls, one layer at a time, with protocols that scale to multiple independent agents.

---

## Phase 1 — MCP-ify the tools (signals & pricing rules)

### Concepts
- **MCP (Model Context Protocol)**: a standard way for an LLM/agent to discover and call
  external tools/data, instead of you hardcoding function imports. An MCP **server** exposes
  capabilities; an MCP **client** (embedded in your agent) connects to it and calls them.
- **Tools vs Resources vs Prompts**: MCP servers can expose callable *tools* (functions with
  a JSON schema, e.g. `get_weather(city)`), *resources* (readable data, e.g. a file or DB
  row), and *prompt templates*. You'll only need tools for now.
- **Transport**: MCP servers talk over stdio (simplest, good for local dev) or
  streamable HTTP (needed once agents run as separate services in later phases).
- **Why bother** if it's still one process? Because it forces you to define explicit,
  typed contracts for your tools now — the exact same contracts you'll need when these
  tools move to a separate service in Phase 3.

### Tasks
1. `pip install mcp` (the official Python MCP SDK) and add it to `requirements.txt`.
2. Create `mcp_server/signals_server.py`. Use `FastMCP` to expose:
   - `get_weather(city: str) -> str` (wraps `agents/signal_collector.py`)
   - `get_vendor_price(base_price, inventory_stock, competitor_price, expiry_date) -> float`
     (wraps `adaptors/vendor_adaptors.py`)
3. Run it standalone over stdio: `mcp dev mcp_server/signals_server.py` — use the MCP
   Inspector (comes with the SDK) to call each tool manually and see the JSON in/out.
4. Write `mcp_server/client.py`: a small MCP client that connects to your server and calls
   both tools. Confirm you get identical results to calling the Python functions directly.
5. Rewire `agents/pricing_strategist.py` to fetch signals/prices through the MCP client
   instead of importing `signal_collector` / `vendor_adaptors` directly.

### Validation
Run `main.py` end-to-end and confirm the recommendation output is unchanged — you've only
swapped the transport, not the logic. Also run the MCP Inspector independently and confirm
both tools show correct input schemas.

### Reflection
- What would break if `get_weather` were called by two different agents at once — does
  anything about MCP change how you'd handle that?
- Why is a JSON schema on the tool more useful to an LLM than a plain Python function
  signature?

---

## Phase 2 — Orchestrate with LangGraph

### Concepts
- **State graph**: LangGraph models an agent as nodes (steps) and edges (transitions) over
  a shared, typed **state** object — instead of a linear function with nested try/except.
- **Conditional edges**: routing decisions (e.g. "Groq succeeded → format response" vs
  "Groq failed → run mock fallback") become explicit graph edges you can visualize, not
  buried in exception handling.
- **Checkpointing**: LangGraph can persist state between steps (and across runs), which
  matters once a graph has retries or human-in-the-loop steps. Not critical yet, but note
  where it would plug in.

### Tasks
1. `pip install langgraph` and add to `requirements.txt`.
2. Define a `PricingState` (TypedDict or Pydantic model): `product_name`, `base_price`,
   `cost_price`, `inventory_stock`, `competitor_price`, `expiry_date`, `city`,
   `weather_signal`, `rule_based_price`, `llm_recommendation`, `error`.
3. Convert the current linear flow in `suggest_adaptive_price()` into nodes:
   - `fetch_signals` (calls the Phase 1 MCP client for weather)
   - `compute_rule_based_price` (calls the Phase 1 MCP client for vendor price)
   - `call_llm` (Groq call)
   - `fallback_mock` (the existing `generate_mock_pricing_decision`)
4. Wire a conditional edge: `call_llm` → `fallback_mock` only if the LLM call raises/fails.
5. Compile the graph and call it from `main.py` instead of `suggest_adaptive_price()`
   directly.

### Validation
Print/visualize the compiled graph (LangGraph can render a Mermaid diagram of it) and
confirm it matches your mental model. Force a Groq failure (bad API key) and confirm the
conditional edge routes to the mock fallback correctly.

### Reflection
- What part of your current `try/except` fallback logic was actually a routing decision in
  disguise?
- Where would a retry node go if Groq returned a 429 (rate limit) instead of failing
  outright?

---

## Phase 3 — First A2A agent: cab pricing as an independent service

### Concepts
- **A2A (Agent2Agent protocol)**: where MCP standardizes agent→tool calls, A2A
  standardizes **agent→agent** calls between independent, possibly differently-owned
  services. Each agent publishes an **Agent Card** (JSON, typically at
  `/.well-known/agent-card.json`) describing its skills, so a caller can discover what it
  does before invoking it.
- **Task lifecycle**: A2A calls are modeled as tasks with states (submitted → working →
  completed/failed), not a single request/response — built for longer-running or
  multi-turn agent work.
- **Why this matters for your project**: this is where "independent pipelines" (your
  earlier architecture decision) becomes literal — the cab pricing agent is a separate
  running process/service, not a Python import.

### Tasks
1. Create a new service directory `agents_a2a/cab_agent/` with its own FastAPI app.
2. Give the cab agent its own MCP tools (in a new `mcp_server/cab_signals_server.py`) for
   cab-specific signals: demand level, traffic/ETA, surge zone — mocked data is fine to
   start.
3. Implement the cab agent's pricing logic (its own `adaptors/cab_adaptor.py`, following
   the same interface shape as `vendor_adaptors.get_vendor_price`, but with surge/demand
   rules instead of expiry/inventory).
4. Publish an Agent Card for it (`/.well-known/agent-card.json`) describing a `price_cab_ride`
   skill.
5. Add an A2A client to your Phase 2 LangGraph orchestrator: a new node that discovers the
   cab agent via its Agent Card and delegates a pricing task to it over HTTP, instead of
   importing it as a module.
6. Run both services locally on different ports and confirm the orchestrator can reach the
   cab agent purely over the network.

### Validation
Kill the cab agent process and confirm the orchestrator's A2A call fails gracefully (this
is the same category of failure-handling you built in Phase 2 — reuse that instinct).
With it running, confirm a full request → discovery → delegation → response round trip.

### Reflection
- What's the actual difference between what MCP gave you in Phase 1 and what A2A gives you
  here? (If your answer is "not much," re-read — one is tool-calling, the other is
  peer-agent delegation with its own task lifecycle.)
- What would you need to add for the orchestrator to handle *two* cab-like agents run by
  different teams with slightly different Agent Cards?

---

## Phase 4 — Repeat the template: porter, bike-taxi, house/stay

By now the pattern is mechanical: MCP tools for domain signals → adaptor with domain
pricing rules → FastAPI + Agent Card → orchestrator delegates via A2A.

### Tasks
Do this yourself as the exercise — design the domain signals for each vertical before
writing code:

| Vertical | Plausible signals | What replaces "expiry"? | What replaces "inventory"? |
|---|---|---|---|
| Porter | distance, item weight/fragility, time of day | — | available porters nearby |
| Bike taxi | traffic, distance, weather, time of day | — | available riders nearby |
| House/stay | seasonality, local events, occupancy rate | booking lead time | available room-nights |

Build one vertical fully before starting the next — resist copy-pasting all four at once.

### Validation
All four verticals (retail, cab, porter, bike-taxi, stay — plus house/stay) run as separate
services, each independently testable via its Agent Card + MCP Inspector, and the
orchestrator can delegate to any of them.

---

## Phase 5 — Structured outputs with Pydantic

### Concepts
- Free-text LLM responses (what you have now) are fine for a human to read but unusable
  for another agent to consume programmatically. Structured output — via Pydantic schemas
  passed as a tool/function-calling schema — makes the LLM return typed, validated JSON.

### Tasks
1. Define `PricingDecision(BaseModel)`: `final_price: float`, `profit_margin: float`,
   `reasoning: str`, `confidence: Literal["low","medium","high"]`.
2. Update the Groq call to use structured/function-calling output constrained to this
   schema (check current Groq API support for JSON schema mode).
3. Update every node/agent in the graph to pass `PricingDecision` objects between them
   instead of raw strings.

### Validation
Feed a malformed/edge-case input (e.g. negative inventory) and confirm Pydantic raises a
validation error you can catch, rather than silently passing bad data downstream.

---

## Phase 6 — Observability (LangSmith or Langfuse)

### Concepts
- Once you have MCP calls + A2A delegation + LangGraph nodes, "why did it return this
  price" becomes hard to answer by reading logs. Tracing tools capture every step as a
  span in a timeline you can inspect.

### Tasks
1. Pick one: LangSmith (native LangGraph integration) or Langfuse (open-source,
   self-hostable).
2. Instrument the orchestrator graph and at least one A2A call so a single pricing request
   shows up as one trace with nested spans (fetch signals → compute rule price → call LLM
   → delegate to cab agent → final decision).

### Validation
Trigger one request and find its full trace in the tool's UI, confirming you can see
timing and inputs/outputs for every node and every cross-agent call.

---

## Phase 7 — Docker Compose: real multi-service deployment

### Tasks
1. Write a `Dockerfile` per service (each MCP server, each vertical A2A agent, the
   orchestrator).
2. Write `docker-compose.yml` wiring them together on a shared network, with the
   orchestrator as the entry point.
3. Confirm the entire system comes up with `docker compose up` and a pricing request
   flows through all the real network boundaries you designed in Phases 1–4.

### Reflection
- Now that every boundary in your diagram is a real network call, what's the first thing
  you'd add for resilience (timeouts, retries, circuit breaker)?

---

## Reference links to read alongside each phase

- MCP spec & Python SDK: modelcontextprotocol.io
- LangGraph docs: langchain-ai.github.io/langgraph
- A2A protocol spec: a2a-protocol.org (Linux Foundation project)
- Pydantic docs: docs.pydantic.dev
- LangSmith: docs.smith.langchain.com / Langfuse: langfuse.com/docs
