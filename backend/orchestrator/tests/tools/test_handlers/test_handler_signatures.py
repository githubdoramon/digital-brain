"""
Guard tests for tool handler call compatibility.

The controller passes shared runtime context kwargs to every handler:
- state
- question
- search_limit
- user_email
- conversation_history

Handlers must either accept these explicit kwargs or include **kwargs.
"""

import inspect

from tools.handlers import HANDLERS

CONTROLLER_CONTEXT_KWARGS = (
    "state",
    "question",
    "search_limit",
    "user_email",
    "conversation_history",
)


def test_all_registered_handlers_accept_controller_context_kwargs():
    """
    Ensure every registered handler can accept the controller's keyword args.

    This prevents runtime failures like:
    "got an unexpected keyword argument 'user_email'".
    """
    incompatible: list[str] = []

    for tool_name, handler in HANDLERS.items():
        signature = inspect.signature(handler)
        parameters = signature.parameters
        accepts_var_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()
        )

        if accepts_var_kwargs:
            continue

        missing = [kw for kw in CONTROLLER_CONTEXT_KWARGS if kw not in parameters]
        if missing:
            incompatible.append(
                f"{tool_name} ({handler.__name__}) missing kwargs: {', '.join(missing)}"
            )

    assert not incompatible, (
        "Handlers must accept controller context kwargs; fix by adding explicit kwargs "
        "or **kwargs:\n" + "\n".join(incompatible)
    )
