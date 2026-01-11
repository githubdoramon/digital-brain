---
name: contact-lookup
description: Find and analyze contact information, map relationships between people, resolve names and aliases, and explore social networks.
---

# Contact Lookup

When the user asks about people, contacts, or relationships, use the `lookup_contact` tool. It handles fuzzy matching, name variations, aliases, and returns relationship data for you to interpret.

## Primary Tool: `lookup_contact`

### Action: `search`

Find contacts by name, email, or phone with intelligent fuzzy matching.

```json
{
  "action": "search",
  "query": "John Smith"
}
```

Parameters:

- `query` (required): Name, email, phone, or partial match
- `search_by`: "name", "email", "phone", or "any" (default)
- `fuzzy_threshold`: 0-100, lower is more lenient (default 75)
- `limit`: Max results to return (default 10)

**Handles automatically:**

- Partial names ("John" finds "John Smith")
- Nicknames and aliases
- Typos and variations via fuzzy matching
- Case-insensitive matching
- Email/phone lookups

### Action: `get_relationships`

Get a contact's relationships with full details.

```json
{
  "action": "get_relationships",
  "query": "John Smith"
}
```

Or with contact_id if you have it:

```json
{
  "action": "get_relationships",
  "contact_id": "contact:john-smith"
}
```

Parameters:

- `contact_id` or `query` (one required)
- `relationship_types`: Optional array to filter (e.g., `["father", "mother"]`)

### Action: `find_related`

Find a contact AND their related contacts in one call.

```json
{
  "action": "find_related",
  "query": "John"
}
```

Parameters:

- `query` (required): Search for the primary contact
- `relationship_types`: Optional filter for specific types

## Examples

### "Who is John Smith?"

```json
{
  "action": "search",
  "query": "John Smith"
}
```

### "How do I know Maria?"

```json
{
  "action": "get_relationships",
  "query": "Maria"
}
```

Returns all relationships for Maria - inspect the `type` field to understand how she's connected.

### "Who reports to David?"

```json
{
  "action": "find_related",
  "query": "David",
  "relationship_types": ["report", "direct-report"]
}
```

### "List Paula's family"

```json
{
  "action": "find_related",
  "query": "Paula"
}
```

Then filter the results by relationship types that indicate family (father, mother, son, daughter, spouse, sibling, etc.).

### "Find John's phone number"

```json
{
  "action": "search",
  "query": "John"
}
```

The response includes `phones` array for each matching contact.

### "Who has this email: john@example.com?"

```json
{
  "action": "search",
  "query": "john@example.com",
  "search_by": "email"
}
```

## Response Format

### Search Response

```json
{
  "action": "search",
  "found": true,
  "count": 2,
  "contacts": [
    {
      "contact_id": "contact:john-smith",
      "display_name": "John Smith",
      "aliases": ["Johnny", "JS"],
      "emails": ["john@example.com"],
      "phones": ["+1-555-0100"],
      "tags": ["colleague", "engineering"],
      "relationships": [...],
      "match_score": 100,
      "match_reason": "exact name match: john smith"
    }
  ]
}
```

### Find Related Response

```json
{
  "action": "find_related",
  "found": true,
  "primary_contact": {
    "contact_id": "contact:paula",
    "display_name": "Paula",
    "match_score": 100,
    "match_reason": "exact name match: paula"
  },
  "related_contacts": [
    {
      "type": "daughter",
      "contact_id": "contact:maria",
      "related_contact": {
        "display_name": "Maria",
        "emails": ["maria@example.com"]
      }
    },
    {
      "type": "husband",
      "contact_id": "contact:carlos",
      "related_contact": {
        "display_name": "Carlos"
      }
    }
  ],
  "relationship_count": 5
}
```

## Tips

- Use `find_related` for "X's family/colleagues" questions - it does search + relationship lookup in one call
- The tool handles name variations automatically - "Jon" will match "John" with fuzzy matching
- Aliases are searched too - if someone goes by "Mike" but their display name is "Michael", both work
- Lower `fuzzy_threshold` (e.g., 50) for more lenient matching if initial search fails
- Relationships include a `type` field - use this to filter relevant ones based on the user's question
- Check `match_reason` to understand why a contact was matched
- Never expose raw contact_ids to users - use display names

## Fallback: SQL Queries

For complex queries not covered by `lookup_contact`, use `execute_sql`:

```sql
-- Find contacts with specific tags
SELECT display_name, emails, tags
FROM contacts
WHERE 'engineering' = ANY(tags)
ORDER BY display_name;
```

Use `describe_schema` first to validate column names.
