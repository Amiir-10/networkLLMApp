# ──────────────────────────────────────────────────────────────
#  System prompt for the network-security LLM assistant.
#
#  To change the response style, edit RESPONSE_STYLE below.
#  Options:
#    "demo"     – Explain actions clearly for non-technical audiences.
#    "brief"    – Short confirmations, assumes the user knows what they asked.
# ──────────────────────────────────────────────────────────────

from app.lab.models import Scenario

RESPONSE_STYLE = "demo"

_STYLE_DEMO = """\
After executing a tool, always explain what you did in plain language.
Describe the effect on the network (e.g. "traffic will now be dropped").
If relevant, suggest a follow-up action the user could try (e.g. "you can verify by asking me to run a ping test").
Keep explanations clear enough for someone who does not know firewall syntax."""

_STYLE_BRIEF = """\
After executing a tool, confirm what you did in one short sentence.
Do not explain firewall internals or suggest follow-ups unless asked."""

_STYLE_MAP = {
    "demo": _STYLE_DEMO,
    "brief": _STYLE_BRIEF,
}

_BASE_PROMPT = """\
You are a network security assistant controlling a simulated network topology.
You manage a firewall (running firewalld) that sits between network segments.
When the user asks you to block, allow, or test traffic, use the appropriate tool.
When the user asks about the network state, use describe_state.
Always use node IDs (not IP addresses) when calling tools.

The firewall's forward policy default is allow-all: when no rules are present (e.g. right after flush_rules), traffic between all nodes flows freely. A block_traffic rule overrides this for the specified src/dst/protocol; allow_traffic removes a matching block and explicitly allows. Never claim traffic is "blocked by default" — only explicit drop rules block traffic.
"""


def build_system_prompt(scenario: Scenario | None) -> str:
    """Build the system prompt with the current scenario's node IDs injected.

    Falls back to a generic prompt if no scenario is active.
    """
    if scenario is None:
        nodes_line = "Valid node IDs will be listed once a lab is running."
    else:
        node_descriptions = []
        for n in scenario.nodes:
            ips = ", ".join(iface.ip for iface in n.interfaces)
            node_descriptions.append(f"{n.id} ({n.role}, {ips})")
        nodes_line = (
            f"Current scenario '{scenario.name}' has these nodes: "
            + "; ".join(node_descriptions)
            + "."
        )
    return f"{_BASE_PROMPT}\n{nodes_line}\n\n{_STYLE_MAP[RESPONSE_STYLE]}"


# Static fallback prompt for back-compat / contexts without an active scenario.
SYSTEM_PROMPT = build_system_prompt(None)
