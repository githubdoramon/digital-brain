from __future__ import annotations

from prompts.clarification import append_clarification_guidelines


def build_people_extraction_prompt(
    *,
    text: str,
    conversation_block: str,
    user_facts_block: str,
) -> str:
    prompt = f"""Extract all person references from this text.

Text: \"{text}\"

{conversation_block}{user_facts_block}

IMPORTANT CONTEXT USAGE:
- Focus on the current Text above.
- Use Conversation messages only to resolve references inside this Text.
- Do NOT include people that appear only in conversation history.
- If the text is an analytical question asking for an unknown person (e.g., \"who did I meet most\"), do NOT output placeholders.

Extract ONLY people references including:
- Proper names (e.g., \"John Smith\")
- Relational terms (e.g., \"my daughter\", \"the doctor\")
- Nested relationships when clear (e.g., \"my daughter's doctor\")
- The current user as \"user\" IF THEY are an active participant in the event (referred to, for example, as "I", "me", "my", "mine", "myself", "we", "us", "our", "ours")

Normalization rules:
- If text has \"X's <corporate/professional title>\" where X is an organization/company/team, output ONE person mention formatted as \"<title> at X\".
- If a proper name and a relationship/profession clearly describe the SAME person in the same clause, return ONLY the proper name.
- If a generic role is later identified by a specific name in the same text, return ONLY the named person for that role.
- Keep possessive markers in relationship phrases: \"my daughter\", not \"daughter\".
- For named collective relationship-group phrases like \"John's whole family\" or \"Paul's work buddies\", include BOTH the anchor person and the possessive group phrase exactly as written.
- Do NOT include second-person pronouns.
- Do NOT include non-specific placeholders like \"the person\", \"someone\", \"anybody\".

Pronoun resolution rules:
- Resolve possessive pronouns only when the referent is crystal clear and creates a valid person reference.
- If a possessive pronoun cannot be resolved confidently, omit that ambiguous reference.

OUTPUT CONTRACT:
- Return exactly one top-level JSON object.
- The response_format schema is authoritative; follow it exactly.
- The object MUST contain exactly one property: \"people\".
- \"people\" MUST be an array of strings.
- Do NOT use alternate property names such as \"people_references\", \"persons\", \"contacts\", \"names\", or \"results\".
- Do NOT wrap people in objects like {{\"name\": \"...\"}}.
- Do NOT include markdown fences, explanations, reasoning, or any extra keys.
- Valid example: {{\"people\": [\"John Smith\", \"my daughter\"]}}
- Invalid example: {{\"people_references\": [{{\"name\": \"John Smith\"}}]}}

Return ONLY a JSON object matching the supplied response schema."""
    return append_clarification_guidelines(prompt)


def build_collective_selector_prompt(
    *,
    text: str,
    conversation_block: str,
) -> str:
    prompt = f"""Extract collective participant selectors from this text.

Text: \"{text}\"

{conversation_block}Rules:
- Return only collective participant selectors, not individual people.
- Allowed selector kinds: email_domain, company, group, tag.
- Examples:
  * \"everyone with @acme.example\" -> {{\"kind\":\"email_domain\",\"value\":\"acme.example\",\"raw\":\"@acme.example\",\"deterministic\":true}}
  * \"everyone from company Acme\" -> {{\"kind\":\"company\",\"value\":\"Acme\",\"raw\":\"everyone from company Acme\",\"deterministic\":true}}
  * \"all people from my soccer team\" -> {{\"kind\":\"group\",\"value\":\"soccer team\",\"raw\":\"my soccer team\",\"deterministic\":false}}
- Do not use family/relationship groups as collective selectors.
- Do NOT infer a collective selector from a singular organization mention like \"I was fired from Acme\" or \"I met Pat at Acme\".
- Only return a selector when the text explicitly refers to a plural or collective set such as \"everyone\", \"all\", \"team\", \"people\", \"staff\", or \"employees\".
- Do NOT return vague crowd references like \"lots of people\", \"many people\", \"some people\", \"a bunch of people\", or \"the people there\". Those are not resolvable selectors.

OUTPUT CONTRACT:
- Return exactly one top-level JSON object.
- The response_format schema is authoritative; follow it exactly.
- The object MUST contain exactly one property: \"selectors\".
- \"selectors\" MUST be an array.
- Each selector item MUST contain exactly: \"kind\", \"value\", \"raw\", \"deterministic\".
- Do NOT return a bare array, markdown fences, explanations, or any extra keys.

Return ONLY a JSON object matching the supplied response schema."""
    return append_clarification_guidelines(prompt)


def build_nested_relationship_selection_prompt(
    *,
    anchor_display_name: str,
    relationship_phrase: str,
    candidate_block: str,
) -> str:
    prompt = f"""Choose which related contacts match a nested relationship mention.

Anchor person: \"{anchor_display_name}\"
Nested relationship phrase: \"{relationship_phrase}\"

Candidate related contacts:
{candidate_block}

Rules:
1. You MUST select only from the listed candidates.
2. Return one or more candidate numbers when the phrase clearly refers to those people.
3. For group/collective mentions, multiple selections are allowed.
4. If no candidate matches, return an empty list.
5. Set collective_reference=true only when the phrase implies multiple people.

Return ONLY a JSON object matching the supplied response schema."""
    return append_clarification_guidelines(prompt)


def build_contact_disambiguation_prompt(
    *,
    person_text: str,
    candidate_list: str,
    event_context: str,
    disambiguation_history_block: str,
    conversation_block: str,
    user_facts_block: str,
) -> str:
    prompt = f"""Disambiguate a person reference from the list of candidates.

Person you are trying to find: \"{person_text}\"

Candidates:
{candidate_list}

Event context (use only if it is relevant): \"{event_context}\"

{disambiguation_history_block}{conversation_block}{user_facts_block}

Interpretation hints:
- Treat the latest user message as the clarification answer to the latest assistant question.
- If context explicitly indicates the person is not in the candidate list and is a new person, set \"new_contact\": true.
- If context explicitly says the person should not be added to the event or was not part of it, prefer \"cannot_decide\" with \"new_contact\": false.
- Do not ignore explicit user clarification even if name similarity exists.
- When candidate relationship evidence conflicts with weak name/alias similarity, prefer the relationship evidence.

CRITICAL RULES:
- Choose ONLY from the candidates above or say \"cannot_decide\".
- Do NOT invent a person, candidate, or extra fields.
- If there is a perfect match, return decision=\"resolved\" and the candidate number of the correct candidate from the numbered list.
- If the user clarified a full or partial candidate name, choose that candidate and return its candidate_number.
- If context is not enough, return decision=\"cannot_decide\".
- Set \"new_contact\" to true ONLY when you are certain the mention refers to a new contact not present in candidates.

OUTPUT CONTRACT:
- Return exactly one top-level JSON object.
- The response_format schema is authoritative; follow it exactly.
- The object MUST contain exactly these properties: \"decision\", \"candidate_number\", \"new_contact\", \"confidence\", \"reasoning\".
- Allowed decision values: \"resolved\" or \"cannot_decide\".
- If decision=\"resolved\", candidate_number MUST be an integer from the candidate list.
- If decision=\"cannot_decide\", candidate_number MUST be null.
- Do NOT use alternate keys such as \"resolved\", \"status\", \"candidate_id\", \"resolution\", or \"clarification\".
- Do NOT include markdown fences, extra nesting, or extra keys.
- Valid example: {{\"decision\":\"resolved\",\"candidate_number\":1,\"new_contact\":false,\"confidence\":\"high\",\"reasoning\":\"Alias and context match.\"}}

Return ONLY a JSON object matching the supplied response schema, with no other text."""
    return append_clarification_guidelines(prompt)


def build_profession_inference_prompt(*, person_text: str, full_text: str) -> str:
    prompt = f"""Infer profession from context. If a general term is provided, convert to a more offical term as well.

Text: \"{full_text}\"
Person in the text you should infer the profession for: \"{person_text}\"

CRITICAL: Only return profession for the person in context if EXPLICITLY stated or STRONGLY implied (e.g., \"Dr.\" prefix).
Otherwise return null.

Return ONLY a JSON object matching the supplied response schema, with no other text."""
    return append_clarification_guidelines(prompt)


def build_relationship_pairs_prompt(*, full_text: str, people_list: str) -> str:
    prompt = f"""Infer explicit relationship pairs between mentioned people.

Text: \"{full_text}\"
People mentions (exact strings):
{people_list}

Rules:
- Only include relationships explicitly stated in the text.
- Only include durable interpersonal relationships or meaningful real-world roles.
- Do NOT include temporary event roles or co-presence labels such as guest, host, attendee, participant, or visitor.
- \"person_text\" and \"anchor_text\" must be in the list above or \"user\".
- \"relationship_hint\" should be the role/profession/relationship term (e.g., \"neurologist\", \"teacher\", \"mother\", \"personal trainer\").
- Prefer specific types over general terms WHEN POSSIBLE (e.g., \"Electric Engineer\" over \"Engineer\", \"Orthopedist\" over \"Doctor\").
- Do NOT include self-relations.
- Do NOT include duplicate pairs.

OUTPUT CONTRACT:
- Return exactly one top-level JSON object.
- The response_format schema is authoritative; follow it exactly.
- The object MUST contain exactly one property: \"relationships\".
- \"relationships\" MUST be an array.
- Each relationship item MUST contain exactly: \"person_text\", \"anchor_text\", \"relationship_hint\".
- If there are no relationships, return {{\"relationships\": []}}.
- Do NOT return a bare array, empty object, markdown fences, or extra keys.

Return ONLY a JSON object matching the supplied response schema."""
    return append_clarification_guidelines(prompt)


def build_relationship_types_prompt(
    *,
    person_text: str,
    anchor_text: str,
    relationship_hint: str,
    full_text: str,
    person_profession: str | None,
    anchor_profession: str | None,
) -> str:
    prompt = f"""Suggest relationship types between two people.

Person A: \"{person_text}\"
Person B: \"{anchor_text}\"
Relationship hint: \"{relationship_hint}\"
Person A profession (if known): \"{person_profession}\"
Person B profession (if known): \"{anchor_profession}\"
Full context: \"{full_text}\"

Rules:
- If the relationship hint does not indicate a relationship, return nulls.
- Do NOT convert temporary event roles or co-presence labels into saved relationships. Reject hints like guest, host, attendee, participant, or visitor.
- \"type\" is what Person A is to Person B.
- \"other_type\" is what Person B is to Person A.
- Use concise, lowercase terms.
- NEVER return self-relations (no \"self\", \"same person\", or equivalent).
- Prefer more offical term over general term WHEN POSSIBLE (e.g., \"Orthopedist\" over \"bone doctor\").

Return ONLY a JSON object matching the supplied response schema."""
    return append_clarification_guidelines(prompt)
