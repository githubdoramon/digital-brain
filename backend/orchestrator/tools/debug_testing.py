"""Tools-page-only debug tool helpers.

These helpers are intentionally not registered in the shared agent tool registry,
so they can be exercised from `/tools` without becoming model-visible tools.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from commands.event import event_pending_key
from commands.handlers.event import handle_event
from commands.parser import ParsedCommand
from tools.contracts import ToolContract, ToolParameter
from ui_dsl import command_result_to_ui_directives


def _normalize_event_creation_flow_args(args: dict[str, Any]) -> dict[str, Any]:
    prompt = str(args.get("prompt") or "").strip()
    normalized: dict[str, Any] = {"prompt": prompt}

    thread_id = str(args.get("thread_id") or "").strip()
    if thread_id:
        normalized["thread_id"] = thread_id

    client_context = args.get("client_context")
    if isinstance(client_context, dict):
        normalized["client_context"] = client_context

    return normalized


EVENT_CREATION_FLOW_CONTRACT = ToolContract(
    name="event_creation_flow",
    description=(
        "Run the `/event` extraction and preview flow directly from the tools page. "
        "Accepts a natural-language event prompt and returns the command payload, preview state, "
        "and derived UI directives for debugging."
    ),
    parameters=[
        ToolParameter(
            name="prompt",
            type="string",
            description="Natural-language event prompt to feed into the `/event` creation flow.",
            required=True,
            min_length=1,
        ),
        ToolParameter(
            name="thread_id",
            type="string",
            description=(
                "Optional debug thread identifier. Reuse it when you want stable pending-event state across repeated runs."
            ),
            required=False,
        ),
        ToolParameter(
            name="client_context",
            type="object",
            description="Optional client context payload forwarded to the event command flow.",
            required=False,
        ),
    ],
    normalizer=_normalize_event_creation_flow_args,
)


TOOLS_PAGE_ONLY_CONTRACTS: dict[str, ToolContract] = {
    EVENT_CREATION_FLOW_CONTRACT.name: EVENT_CREATION_FLOW_CONTRACT,
}


def get_tools_page_only_contract(tool_name: str) -> ToolContract | None:
    return TOOLS_PAGE_ONLY_CONTRACTS.get(tool_name)


def run_tools_page_only_tool(
    tool_name: str,
    normalized_args: dict[str, Any],
    *,
    user_email: str,
) -> dict[str, Any]:
    if tool_name != EVENT_CREATION_FLOW_CONTRACT.name:
        raise ValueError(f"Unknown tools-page-only tool: {tool_name}")

    prompt = str(normalized_args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt is required")

    thread_id = str(normalized_args.get("thread_id") or "").strip() or f"tool-event-{uuid4().hex[:10]}"
    pending_key = event_pending_key(user_email, thread_id)
    client_context = normalized_args.get("client_context")

    command_result = handle_event(
        ParsedCommand(command="event", args=prompt, raw_message=f"/event {prompt}"),
        {
            "user_email": user_email,
            "thread_id": thread_id,
            "event_pending_key": pending_key,
            "client_context": client_context if isinstance(client_context, dict) else None,
            "progress_callback": None,
        },
    )

    return {
        "command_result": command_result,
        "ui_directives": command_result_to_ui_directives(command_result),
        "debug_context": {
            "thread_id": thread_id,
            "pending_event_key": pending_key,
            "log_hint": "Inspect backend runtime logs to follow the event extraction and resolution decisions.",
        },
    }


__all__ = [
    "EVENT_CREATION_FLOW_CONTRACT",
    "TOOLS_PAGE_ONLY_CONTRACTS",
    "get_tools_page_only_contract",
    "run_tools_page_only_tool",
]
