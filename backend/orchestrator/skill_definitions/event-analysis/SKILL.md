---
name: event-analysis
description: Analyze events, moments, meetings, extract attendees, summaries/content, identify patterns, and summarize topics from calendar events and meeting notes. Find out what happened.
---

# Event Analysis

When the user asks about meetings, calls, calendar events, or anything that happened in the past, follow these steps to provide comprehensive analysis.

## Step 1: Identify Time Range and People

- Parse any time references in the question (e.g., "last week", "in December", "with John yesterday")
- Use `lookup_contact` to find contacts mentioned by name (handles fuzzy matching, nicknames, partial names)
- Use `resolve_query` to extract dates and places mentioned
- If no time is specified, search without time constraints to find the most relevant results

## Step 2: Search for Events or Documents

- Use `search_memories` with a query focused on what the user wants to know.
- Apply filters for:
  - Time range (time_start, time_end)
  - People mentioned (use contact IDs from lookup_contact search results)
  - Tags (refer to the tag taxonomy provided in context for available tags)
- Start with a reasonable limit (5-10) and expand if needed

## Step 3: Enrich Results

- Use `get_events` or `get_document` to fetch full details for the top matching results
- For each result, note (if available):
  - Title and summary/content
  - Tags
  - Attendees (people array)
  - Date, time, and duration
  - Location if available
- Use `lookup_contact` with action="search" to get display names for any contact IDs in attendee lists

## Step 4: Format Response

When presenting meeting information:
- List events/moments/documents chronologically (newest first unless asked otherwise)
- Always use attendee names, never raw contact IDs
- Summarize key discussion points from event summaries or documents content
- Highlight action items or follow-ups if mentioned
- Group by topic/project if multiple related meetings are found

## Examples

**User**: "Summarize my meetings last week"
- Resolve time to last 7 days
- Search all meetings in that range
- Aggregate common themes and attendees

**User**: "When did I last meet with Sarah?"
- Use `lookup_contact` with action="search", query="Sarah" to find contact
- Search events with that person's contact_id
- Return the most recent meeting with details

**User**: "When was my last eye exam?"
- Search for documents or events with "health" tag and content related to "eye"
- Look for the document or event date
- Return the date and provide comprehensive summary

**User**: "What is the deadline for project moon?"
- Search for memories with "moon"
- Look for deadline-related content on all results
- Extract and present relevant sections


## Tips

- Check both the title, summary and content for relevant information
- If the user asks about a specific topic, search for that topic in addition to people/time filters
