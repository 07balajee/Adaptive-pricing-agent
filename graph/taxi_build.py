from langgraph.graph import StateGraph, START, END

from .taxi_state import TaxiState
from .taxi_nodes import load_history, fetch_route, fetch_weather, price_trip, call_llm, fallback_advice


def route_after_llm(state: TaxiState) -> str:
    return "fallback_advice" if state.get("error") else END


def build_taxi_graph():
    """Invoke with config={"configurable": {"signals": <TaxiSignals>}} (see TaxiSignalsClient.connect)."""
    g = StateGraph(TaxiState)
    g.add_node("load_history", load_history)
    g.add_node("fetch_route", fetch_route)
    g.add_node("fetch_weather", fetch_weather)
    g.add_node("price_trip", price_trip)
    g.add_node("call_llm", call_llm)
    g.add_node("fallback_advice", fallback_advice)

    # History loads in parallel with route + weather; price_trip waits for both branches
    g.add_edge(START, "load_history")
    g.add_edge(START, "fetch_route")
    g.add_edge("fetch_route", "fetch_weather")
    g.add_edge(["load_history", "fetch_weather"], "price_trip")
    g.add_edge("price_trip", "call_llm")
    g.add_conditional_edges("call_llm", route_after_llm, {"fallback_advice": "fallback_advice", END: END})
    g.add_edge("fallback_advice", END)
    return g.compile()


if __name__ == "__main__":
    # python -m graph.taxi_build  -> prints the graph as Mermaid
    print(build_taxi_graph().get_graph().draw_mermaid())
