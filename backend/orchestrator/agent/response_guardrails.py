"""Guardrails for malformed or non-actionable LLM text responses."""


CONTINUATION_PROMPT_SYNC = (
    "You expressed intent to perform an action but didn't call any tool. "
    "Please actually invoke the tool now."
)

CONTINUATION_PROMPT_STREAM = (
    "You expressed intent to perform an action but didn't call any tool. "
    "Please invoke the tool now."
)

MALFORMED_TOOL_CALL_PROMPT = (
    "You output JSON instead of making a proper tool call. Do NOT output raw JSON. "
    "Use the home_assistant tool with action='call_tool', tool_name set to the HA tool name "
    "(e.g., 'HassLightSet'), and arguments containing the parameters. Try again."
)

CODE_DESCRIBING_TOOL_PROMPT = (
    "WRONG: You wrote CODE describing a tool call instead of ACTUALLY calling the tool. "
    "Do NOT output code snippets, variable assignments, or descriptions. "
    "You MUST use the tool_call mechanism to invoke tools. "
    "Call the home_assistant tool NOW with action='call_tool', tool_name='HassTurnOff' "
    "(or appropriate tool), and arguments={'name': 'device name'}. "
    "ACTUALLY CALL THE TOOL - don't describe it."
)


def looks_like_continuation(content: str) -> bool:
    """Check if content indicates model wants to continue but didn't call a tool."""
    patterns = [
        "let me try", "let me find", "let me search",
        "let me check", "let me look", "let me query",
        "i need to", "i will try", "i'll try",
        "i will search", "i'll search", "i will query",
        "first, i need", "i should", "i'll need to",
    ]

    lower = content.lower().strip()
    if len(lower) > 800:
        return False

    return any(pattern in lower for pattern in patterns)


def looks_like_malformed_tool_call(content: str) -> bool:
    """Check if content looks like a malformed tool call (JSON instead of tool_call)."""
    stripped = content.strip()

    for prefix in ["<|python_tag|>", "```json", "```", "<tool_call>", "<function_call>"]:
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):].strip()
    for suffix in ["```", "</tool_call>", "</function_call>"]:
        if stripped.endswith(suffix):
            stripped = stripped[:-len(suffix)].strip()

    if stripped.startswith("{") and stripped.endswith("}"):
        tool_indicators = [
            '"type": "function"',
            '"name":',
            '"function":',
            '"parameters":',
            '"arguments":',
            '"tool_call"',
            '"action":',
        ]
        return any(indicator in stripped for indicator in tool_indicators)

    return False


def looks_like_code_describing_tool(content: str) -> bool:
    """Check if content describes tool call code instead of issuing a real tool call."""
    lower = content.lower()
    code_indicators = [
        "action = ",
        "action=",
        "tool_name = ",
        "tool_name=",
        "arguments = ",
        "arguments=",
        "'call_tool'",
        '"call_tool"',
        "'list_tools'",
        '"list_tools"',
    ]

    has_code_block = "```" in content or "action =" in lower or "tool_name =" in lower
    has_tool_reference = any(indicator in lower for indicator in code_indicators)

    ha_tools_as_strings = [
        "'hassturnoff'", '"hassturnoff"',
        "'hassturnon'", '"hassturnon"',
        "'hasslightset'", '"hasslightset"',
        "'hassclimatesettemperature'", '"hassclimatesettemperature"',
    ]
    has_ha_tool_string = any(tool in lower for tool in ha_tools_as_strings)

    description_phrases = [
        "here is the code",
        "here's the code",
        "the code is",
        "use this code",
        "code to",
        "i will turn off",
        "i'll turn off",
        "i will turn on",
        "i'll turn on",
    ]
    has_description_phrase = any(phrase in lower for phrase in description_phrases)

    return (
        (has_code_block and has_tool_reference)
        or has_ha_tool_string
        or (has_description_phrase and has_tool_reference)
    )
