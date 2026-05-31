"""Engine layer: topology / security / netconfig.

Single shared surfaces that both the LLM (via app.chat.dispatch_tool) and the
UI/console (via app.main routes) call, so neither path can drift from the other.
"""
