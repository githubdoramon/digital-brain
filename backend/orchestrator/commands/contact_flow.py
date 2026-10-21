"""Natural-language entry into the same contact preview flow as /contact."""

from commands import get_command_registry
from commands.event import event_pending_key
from commands.parser import ParsedCommand
from ui_dsl import command_result_to_ui_directives


def prepare_update(question, user_email, thread_id, client_context, conversation_history=None):
    context = {
        "user_email": user_email,
        "thread_id": thread_id,
        "event_pending_key": event_pending_key(user_email, thread_id),
        "client_context": client_context,
        "reference_history": conversation_history or [],
    }
    result = get_command_registry().execute(
        ParsedCommand(command="contact", args=question, raw_message=question), context
    )
    need = result.get("need_user_input") or {}
    answer = need.get("prompt") or result.get("message") or "Please review the contact update."
    return result, answer, command_result_to_ui_directives(result)
