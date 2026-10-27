from langgraph.graph import StateGraph, START, END

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

    g.add_edge(START, "fetch_signals")
    g.add_edge("fetch_signals", "compute_rule_based_price")
    g.add_edge("compute_rule_based_price", "call_llm")
    g.add_conditional_edges("call_llm", route_after_llm, {"fallback_mock": "fallback_mock", END: END})
    g.add_edge("fallback_mock", END)
    return g.compile()


if __name__ == "__main__":
    # python -m graph.build  -> prints the graph as Mermaid for a quick visual check
    print(build_graph().get_graph().draw_mermaid())
