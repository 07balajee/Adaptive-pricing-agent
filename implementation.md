# Adaptive Pricing Agent — Implementation Plan

Companion to [studyguide.md](studyguide.md): that file teaches the concepts, this file is
the concrete build checklist — exact commands, file trees, and interface skeletons for each
phase. Skeletons intentionally leave the domain logic as `# TODO` — write that part
yourself; the point of scaffolding is the MCP/LangGraph/A2A wiring, not the pricing math
(you've already proven you can write that in `vendor_adaptors.py`).

Work one phase at a time. Do not start a phase until the previous phase's "Definition of
Done" passes.

---

## Phase 1 — MCP-ify the tools

**Objective:** expose `get_weather` and `get_vendor_price` as MCP tools; strategist calls
them through an MCP client instead of direct imports.

**Dependencies**
```
pip install mcp
```
Add `mcp` to `requirements.txt`.

**New/changed files**
```
mcp_server/
  __init__.py
  signals_server.py     # MCP server exposing tools
  client.py              # reusable MCP client wrapper
agents/pricing_strategist.py   # changed: use mcp_server.client instead of direct imports
```

**Execution steps**
1. `mkdir mcp_server` and add `__init__.py`.
2. Build `signals_server.py`:
   ```python
   from mcp.server.fastmcp import FastMCP
   from agents import signal_collector
   from adaptors import vendor_adaptors

   mcp = FastMCP("pricing-signals")

   @mcp.tool()
   def get_weather(city: str, api_key: str) -> str:
       # TODO: call signal_collector.get_weather, return its result
       ...

   @mcp.tool()
   def get_vendor_price(base_price: float, inventory_stock: int,
                         competitor_price: float, expiry_date: str) -> float:
       # TODO: parse expiry_date (str -> date), call vendor_adaptors.get_vendor_price
       ...

   if __name__ == "__main__":
       mcp.run()
   ```
   Note: MCP tool args must be JSON-serializable — `expiry_date` becomes a string over the
   wire, so parse it back to a `date` inside the tool.
3. Smoke-test the server standalone:
   ```
   mcp dev mcp_server/signals_server.py
   ```
   Use the MCP Inspector UI it opens to call both tools manually and confirm outputs match
   what you get calling the Python functions directly in a REPL.
4. Build `client.py` — a thin wrapper so the rest of your code doesn't deal with MCP
   session boilerplate directly:
   ```python
   from mcp import ClientSession, StdioServerParameters
   from mcp.client.stdio import stdio_client

   class SignalsClient:
       def __init__(self, server_script="mcp_server/signals_server.py"):
           self._params = StdioServerParameters(command="python", args=[server_script])

       async def get_weather(self, city: str, api_key: str) -> str:
           # TODO: open stdio_client/ClientSession, call_tool("get_weather", {...}), return result
           ...

       async def get_vendor_price(self, **kwargs) -> float:
           # TODO: same pattern, tool name "get_vendor_price"
           ...
   ```
5. Update `agents/pricing_strategist.py`:
   - Remove the direct `signal_collector` / `vendor_adaptors` imports.
   - Import `SignalsClient`, instantiate once, call it (you'll need to make
     `suggest_adaptive_price` async, or run the client calls via `asyncio.run(...)` at the
     call sites — pick one and be consistent).
6. Update `main.py` if `suggest_adaptive_price` became async (`asyncio.run(main())`
   pattern).

**Verify**
```
python main.py
```
Output should be identical to what you got before this phase (same recommendation logic,
new transport underneath).

**Definition of Done**
- [ ] MCP Inspector shows both tools with correct schemas and returns correct values.
- [x] `main.py` runs end-to-end through the MCP client with unchanged output.
- [x] No remaining direct imports of `signal_collector`/`vendor_adaptors` in
      `pricing_strategist.py`.

---

## Phase 2 — LangGraph orchestration

**Objective:** replace the linear function + try/except fallback in
`suggest_adaptive_price` with an explicit state graph.

**Dependencies**
```
pip install langgraph
```

**New/changed files**
```
graph/
  __init__.py
  state.py         # PricingState definition
  nodes.py         # one function per graph node
  build.py         # graph construction + compile
main.py            # changed: invoke the compiled graph instead of suggest_adaptive_price
```

**Execution steps**
1. `graph/state.py`:
   ```python
   from typing import TypedDict, Optional

   class PricingState(TypedDict):
       product_name: str
       base_price: float
       cost_price: float
       inventory_stock: int
       competitor_price: float
       expiry_date: str
       city: str
       weather_signal: Optional[str]
       rule_based_price: Optional[float]
       llm_recommendation: Optional[str]
       error: Optional[str]
   ```
2. `graph/nodes.py` — one function per step, each taking and returning `PricingState`:
   ```python
   def fetch_signals(state: PricingState) -> PricingState:
       # TODO: call SignalsClient.get_weather, set state["weather_signal"]
       ...

   def compute_rule_based_price(state: PricingState) -> PricingState:
       # TODO: call SignalsClient.get_vendor_price, set state["rule_based_price"]
       ...

   def call_llm(state: PricingState) -> PricingState:
       # TODO: build prompt (reuse existing prompt template), call Groq,
       # set state["llm_recommendation"] or state["error"] on failure
       ...

   def fallback_mock(state: PricingState) -> PricingState:
       # TODO: call generate_mock_pricing_decision, set state["llm_recommendation"]
       ...
   ```
3. `graph/build.py`:
   ```python
   from langgraph.graph import StateGraph, END
   from .state import PricingState
   from .nodes import fetch_signals, compute_rule_based_price, call_llm, fallback_mock

   def route_after_llm(state: PricingState) -> str:
       return "fallback_mock" if state.get("error") else END

   def build_graph():
       g = StateGraph(PricingState)
       g.add_node("fetch_signals", fetch_signals)
       g.add_node("compute_rule_based_price", compute_rule_based_price)
       g.add_node("call_llm", call_llm)
       g.add_node("fallback_mock", fallback_mock)

       g.set_entry_point("fetch_signals")
       g.add_edge("fetch_signals", "compute_rule_based_price")
       g.add_edge("compute_rule_based_price", "call_llm")
       g.add_conditional_edges("call_llm", route_after_llm, {"fallback_mock": "fallback_mock", END: END})
       g.add_edge("fallback_mock", END)
       return g.compile()
   ```
4. Update `main.py` to build the initial `PricingState` dict from your existing inputs,
   invoke `build_graph().invoke(initial_state)`, and print `result["llm_recommendation"]`.
5. Render the graph to confirm the shape:
   ```python
   graph = build_graph()
   print(graph.get_graph().draw_mermaid())
   ```
   Paste the Mermaid output into any Mermaid viewer (or a scratch `.md` file) to eyeball it.

**Verify**
- Run with a valid `GROQ_API_KEY` → confirm it goes straight to `END` after `call_llm`.
- Temporarily break the API key → confirm it routes to `fallback_mock` and still produces
  output.

**Definition of Done**
- [x] Graph compiles and the Mermaid diagram matches your mental model.
- [x] Both the success path and the fallback path produce correct, non-empty output.
- [x] `agents/pricing_strategist.py`'s old nested try/except is gone, replaced by the
      conditional edge.

---

## Phase 3 — First A2A agent: cab pricing

**Objective:** cab pricing runs as an independent, network-reachable agent with its own
Agent Card; the orchestrator delegates to it over A2A instead of importing it.

**Dependencies**
```
pip install fastapi uvicorn httpx
```
(Check whether an official `a2a-sdk` package is current at the time you build this —
if so, prefer it over hand-rolling the protocol; otherwise implement the Agent Card +
task endpoints directly per the A2A spec, which is straightforward JSON-over-HTTP.)

**New/changed files**
```
agents_a2a/
  cab_agent/
    __init__.py
    main.py                 # FastAPI app: agent card + task endpoint
    adaptor.py               # cab-specific pricing rules
mcp_server/
  cab_signals_server.py      # MCP server for cab-specific signals (demand, traffic)
graph/nodes.py                # add: delegate_to_cab_agent node
graph/build.py                 # add node + edges for cab delegation
```

**Execution steps**
1. `agents_a2a/cab_agent/adaptor.py`:
   ```python
   def get_cab_price(base_fare: float, demand_level: str, traffic_level: str,
                      distance_km: float) -> float:
       # TODO: your surge-pricing rules — demand/traffic multipliers instead of
       # expiry/inventory discounts
       ...
   ```
2. `mcp_server/cab_signals_server.py` — same `FastMCP` pattern as Phase 1, exposing
   `get_demand_level(zone: str)` and `get_traffic_level(route: str)` (mocked data is fine).
3. `agents_a2a/cab_agent/main.py`:
   ```python
   from fastapi import FastAPI
   app = FastAPI()

   AGENT_CARD = {
       "name": "cab-pricing-agent",
       "description": "Prices cab rides based on demand, traffic, and distance",
       "skills": [{"id": "price_cab_ride", "description": "..."}],
       "url": "http://localhost:8001",
   }

   @app.get("/.well-known/agent-card.json")
   def agent_card():
       return AGENT_CARD

   @app.post("/tasks/price_cab_ride")
   def price_cab_ride(payload: dict):
       # TODO: pull demand/traffic via cab_signals_server MCP client,
       # call adaptor.get_cab_price, return {"status": "completed", "result": {...}}
       ...
   ```
4. Run it: `uvicorn agents_a2a.cab_agent.main:app --port 8001`.
5. Add `graph/nodes.py::delegate_to_cab_agent`:
   ```python
   import httpx

   async def delegate_to_cab_agent(state: PricingState) -> PricingState:
       # TODO: httpx.get the agent card (or hardcode URL for now),
       # httpx.post to /tasks/price_cab_ride with relevant state fields,
       # store result in state
       ...
   ```
6. Wire it into `graph/build.py` as an alternate entry path — the simplest version: a
   `request_type` field in `PricingState` (`"retail"` vs `"cab"`) picked at the entry point
   via `add_conditional_edges` from `START`.

**Verify**
- Start the cab agent (`:8001`) and the orchestrator separately.
- Hit `http://localhost:8001/.well-known/agent-card.json` directly in a browser/curl —
  confirm valid JSON.
- Run a cab-type request through the orchestrator graph end-to-end.
- Kill the cab agent process, re-run — confirm the orchestrator fails gracefully (caught
  exception / clear error state, not a stack trace crash).

**Definition of Done**
- [ ] Cab agent runs as its own process on its own port.
- [ ] Agent Card is fetchable and well-formed.
- [ ] Orchestrator successfully delegates a cab pricing request over HTTP and gets a
      result back.
- [ ] Orchestrator handles the cab agent being down without crashing.

---

## Phase 4 — Porter, bike-taxi, house/stay

**Objective:** replicate the Phase 3 template three more times.

**Execution steps (per vertical)**
1. Design the signals table entry from `studyguide.md` Phase 4 for this vertical (write it
   down before coding).
2. Copy `agents_a2a/cab_agent/` → `agents_a2a/<vertical>_agent/`, rename, adjust the
   Agent Card skill description.
3. Write `<vertical>_agent/adaptor.py` with vertical-specific rules — this is yours to
   design, not a TODO stub to fill mechanically; the point is deciding what "surge" or
   "discount" means for this domain.
4. Add an MCP signals server for its domain-specific inputs.
5. Add the corresponding `delegate_to_<vertical>_agent` node + routing in
   `graph/build.py`.
6. Assign each agent its own port (`8002`, `8003`, `8004`) and keep a running note of the
   port map in this file or a `PORTS.md`.

**Definition of Done (per vertical, before starting the next one)**
- [ ] Runs standalone, Agent Card fetchable.
- [ ] Orchestrator delegates to it correctly.
- [ ] Orchestrator's routing correctly picks this vertical based on `request_type`.

**Definition of Done (phase overall)**
- [ ] All five agents (retail/original flow + cab + porter + bike-taxi + stay) reachable
      independently and via the orchestrator.

---

## Phase 5 — Structured outputs with Pydantic

**Objective:** every agent returns a typed, validated `PricingDecision` instead of a free
text blob.

**Dependencies**
```
pip install pydantic
```
(likely already present as a LangGraph/FastAPI transitive dependency — pin it explicitly
in `requirements.txt` anyway.)

**New/changed files**
```
schemas/
  __init__.py
  pricing_decision.py
```

**Execution steps**
1. `schemas/pricing_decision.py`:
   ```python
   from pydantic import BaseModel
   from typing import Literal

   class PricingDecision(BaseModel):
       final_price: float
       profit_margin: float
       reasoning: str
       confidence: Literal["low", "medium", "high"]
   ```
2. Check whether your Groq model(s) support constrained/JSON-schema output; if so, pass
   `PricingDecision.model_json_schema()` as the response format. If not supported cleanly,
   prompt for JSON and parse+validate with `PricingDecision.model_validate_json(...)`,
   catching `ValidationError` and retrying once with a stricter prompt.
3. Update every graph node and A2A `/tasks/...` endpoint to produce/consume
   `PricingDecision` (or `.model_dump()` at the network boundary) instead of raw strings.

**Verify**
Feed a deliberately malformed case (e.g. force the LLM to omit a field, or hand-craft a bad
JSON string) and confirm `ValidationError` is caught and handled, not silently passed
downstream.

**Definition of Done**
- [ ] All agents return `PricingDecision`-shaped JSON.
- [ ] Malformed output is caught and retried/handled, not swallowed.

---

## Phase 6 — Observability (LangSmith or Langfuse)

**Objective:** one pricing request produces a single inspectable trace across MCP calls,
graph nodes, and A2A delegation.

**Dependencies**
```
# LangSmith:
pip install langsmith
# or Langfuse:
pip install langfuse
```

**Execution steps**
1. Set the relevant env vars in `.env` (`LANGCHAIN_TRACING_V2=true`, `LANGCHAIN_API_KEY=...`
   for LangSmith; or `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` for Langfuse).
2. LangGraph nodes are auto-traced once tracing env vars are set (LangSmith) — verify a
   run shows up.
3. Manually wrap the A2A `httpx` calls and MCP tool calls with a span
   (`langsmith.trace(...)` context manager, or Langfuse's `@observe()` decorator) so they
   nest under the same trace as the graph run.

**Verify**
Trigger one request, open the tracing UI, and confirm you can see: signal fetch → rule
price → LLM call (or fallback) → cab agent delegation (if applicable) → final decision,
with timing for each.

**Definition of Done**
- [ ] A single request produces one trace with all cross-service calls nested inside it.

---

## Phase 7 — Docker Compose

**Objective:** the whole system runs as separate containers talking over a real network.

**New/changed files**
```
Dockerfile.orchestrator
Dockerfile.cab_agent
Dockerfile.porter_agent
Dockerfile.bike_taxi_agent
Dockerfile.stay_agent
Dockerfile.mcp_signals
docker-compose.yml
```

**Execution steps**
1. Write one minimal `Dockerfile` per service (base `python:3.12-slim`, copy source,
   `pip install -r requirements.txt`, `CMD` to run that service).
2. `docker-compose.yml`: define all services on one network, with the orchestrator's
   config pointing at service names (`http://cab-agent:8001`) instead of `localhost`.
3. `docker compose up --build`.
4. Run a full pricing request against the orchestrator's exposed port.

**Definition of Done**
- [ ] `docker compose up` brings up all services healthy.
- [ ] A request through the orchestrator's public port succeeds end-to-end using only
      container-network addresses internally.

---

## Suggested pacing

| Phase | Focus | Rough effort |
|---|---|---|
| 1 | MCP basics | 1 evening |
| 2 | LangGraph basics | 1–2 evenings |
| 3 | A2A basics (the hard one — new protocol + multi-process debugging) | 2–3 evenings |
| 4 | Repetition/consolidation | 1 evening per vertical |
| 5 | Pydantic structured output | 1 evening |
| 6 | Observability | 1 evening |
| 7 | Docker Compose | 1 evening |

Don't compress phases 1–3 — they're where the actual new concepts live. Phase 4 onward is
mostly applying what you already learned.
