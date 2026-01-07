---
name: contact-lookup
description: Find and analyze contact information, map relationships between people, resolve names and aliases, and explore social networks.
---

# Contact Lookup

When the user asks about people, contacts, or relationships, use this skill to provide detailed information.

## Step 1: Resolve the Contact

- Use `resolve_query` to identify contacts mentioned in the question
- Handle variations: first names, last names, nicknames, partial matches
- If multiple matches exist, clarify with the user or show all options

## Step 2: Fetch Contact Details

Use `execute_sql` to query the contacts table:

```sql
SELECT
    contact_id, display_name, aliases, emails, phones,
    tags, notes, created_at
FROM contacts
WHERE display_name ILIKE '%name%'
   OR EXISTS (SELECT 1 FROM unnest(aliases) AS a WHERE a ILIKE '%name%')
```

## Step 3: Map Relationships

Query the `contact_relationships` table to understand connections:

```sql
SELECT
    cr.from_contact_id, cr.to_contact_id, cr.relationship_type,
    c1.display_name AS from_name, c2.display_name AS to_name
FROM contact_relationships cr
JOIN contacts c1 ON cr.from_contact_id = c1.contact_id
JOIN contacts c2 ON cr.to_contact_id = c2.contact_id
WHERE cr.from_contact_id = 'target_id' OR cr.to_contact_id = 'target_id'
```

Relationship types might include: colleague, friend, family, manager, report, etc.

## Step 4: Find Shared Context

- Search for events where this contact appears in the people array
- Look for documents that mention or are associated with the contact
- Identify common tags or categories

Use `search_memories` to find both events and documents

## Formatting Guidelines

When presenting contact information:
- Lead with the display name
- Show relationships in human-readable form ("John is Sarah's manager")
- Include recent interactions if asked
- Never expose raw IDs to the user

## Examples

**User**: "Who is John Smith?"
- Search contacts for "John Smith"
- Return profile with relationships and recent activity

**User**: "Show me my colleagues"
- Query contacts with "colleague" relationship type or tag
- List with their roles/relationships

**User**: "How do I know Maria?"
- Find Maria's contact
- Query relationship table
- Search events for shared history

**User**: "Who reports to David?"
- Query relationships where David is in manager/supervisor role
- Return list of direct reports

**User**: "List me Paula's family?"
- Query relationships where Paula has any family tags (mother, father, daugther, parent, etc)
- Return list of contacts that are her family

## Tips

- People may have multiple aliases (nicknames, maiden names)
- Relationships are directional: "A manages B" is different from "B managed by A" (or "A is mother of B" is different than "B is mother of A")
- Tags on contacts often indicate their role, how you know them, or even special attributes
- Check the notes field for additional context
