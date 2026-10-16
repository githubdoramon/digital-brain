from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent.state import AgentState


def handle_visual_inspect(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> dict[str, Any]:
    """Inspect a visual source and optionally match people through Immich."""
    from visual_inspection import VisualInspectionError, inspect_visual_source

    include_people_matching = bool(args.get("include_people_matching", False))
    if include_people_matching:
        request_text = str(kwargs.get("question") or (state.goal if state is not None else ""))
        identity_request = re.search(
            r"\b(?:who\s+(?:is|are)\s+(?:this|that|they|these)|who\s+is\s+in\s+(?:this|that)\s+(?:photo|picture|image)|who's\s+(?:this|that)|"
            r"who\s+might\s+this\s+be|identify\s+(?:(?:this|that|the)\s+)?(?:person|people|face|man|woman)|"
            r"recogni[sz]e\s+(?:(?:this|that|the)\s+)?(?:person|people|face)|"
            r"do\s+(?:you|i)\s+know\s+(?:this|that|these|anyone)|"
            r"(?:is|are)\s+(?:this|that|these)\s+(?:(?:someone|anyone)\s+i\s+know|(?:person|people)\s+known\s+to\s+me)|"
            r"someone\s+i\s+know|name\s+(?:this|that)\s+person)\b",
            request_text,
            flags=re.IGNORECASE,
        )
        if not identity_request:
            return {
                "error": "People matching is available only when the user asks to identify someone.",
                "success": False,
            }

    try:
        return inspect_visual_source(
            source_type=str(args.get("source_type") or ""),
            source=str(args.get("source") or ""),
            question=str(args.get("question") or ""),
            user_email=kwargs.get("user_email"),
            attachments=(state.visual_attachments if state is not None else None),
            mime_type=args.get("mime_type"),
            pages=args.get("pages"),
            identify_people=include_people_matching,
        )
    except VisualInspectionError as exc:
        return {"error": str(exc), "success": False}
