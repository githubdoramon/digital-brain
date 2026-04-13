from agents.memory_expert.prompts import get_memory_expert_protocol_prompt


def test_memory_expert_prompt_includes_markdown_table_formatting_guidance():
    prompt = get_memory_expert_protocol_prompt()

    assert "Use markdown tables only when the content is genuinely tabular" in prompt
    assert "Every markdown table row MUST be on its own line" in prompt
    assert "prefer a short heading plus bullets instead of a table" in prompt


def test_memory_expert_prompt_discourages_overfiltered_simple_contact_queries():
    prompt = get_memory_expert_protocol_prompt()

    assert "where did I last meet John?" in prompt
    assert "Do not add `tags` or `types`" in prompt
