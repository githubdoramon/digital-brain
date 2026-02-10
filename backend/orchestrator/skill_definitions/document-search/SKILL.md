---
name: document-search
description: Search and analyze documents stored in the memory system, including PDFs, meeting notes, and other files. Retrieve content, summarize findings, and cross-reference with events and contacts.
---

# Document Search

When the user asks about documents, files, notes, exams or needs information that might be in stored documents, use this skill.

## Step 1: Identify What to Search

Parse the user's question for:
- Document titles or partial names
- Topics or keywords
- Time periods ("documents from last month")
- Related people ("notes from meeting with John")
- Document types (PDF, meeting notes, etc.)

## Step 2: Search Documents

Use `search_memories` with document-focused queries:
- The search includes both events and documents
- Documents have `kind: "document"` in results
- Filter by time if a date range is mentioned

For more specific searches:
- First use `resolve_query` if the question includes people/places/time constraints
- Then call `search_memories` with structured filters (`contact_ids`, time range, tags)
- Keep `query` focused on the semantic topic (not the person's name when `contact_ids` is available)

## Step 3: Retrieve Full Content

Once you identify relevant documents:
- Use `get_document` to retrieve full content
- You can fetch by ID or by title (fuzzy match)
- The tool returns metadata plus the full text content

When the question asks for a specific field/value (lab level, amount, date, identifier):
- Prioritize the top matching document from `search_memories` and call `get_document` before re-searching
- Keep track of already inspected documents; do not discard them after broader follow-up searches
- If extraction is noisy (OCR/PDF ordering), align **label + nearest value + unit + reference range**
- Do not confuse a reference range with the measured value

## Step 4: Analyze and Present

When presenting document findings:
- Summarize the key points relevant to the user's question
- Quote specific passages if they directly answer the question
- Note the document date and source
- Cross-reference with related events or contacts if relevant
- If a relevant document exists but the exact value is uncertain, say that explicitly and explain what was ambiguous instead of saying no record exists

## Examples

**User**: "Find the project proposal we discussed"
- Search documents for "project proposal"
- Retrieve full content of best match
- Summarize key points

**User**: "Show me documents from December"
- Query documents with document_date in December
- List titles and brief descriptions
- Offer to retrieve specific ones

**User**: "What's in the quarterly report?"
- Search for "quarterly report" in title
- Fetch full document content
- Provide comprehensive summary

**User**: "What is the annual rate for my house loan?"
- Search for documents with "finance" tag and related to property/house purchase
- Get the most recent ones by document date
- Retrieve full content for top matches
- Extract and present information related to the question (loan interest rate)


## Document Types

Common document types you might encounter:
- **PDFs**: Reports, contracts, specifications, financial information
- **Text Files**: Quick notes, logs
- **Word Documents**: Formal documents, proposals

## Tips

- Document tags often indicate category or project
- Check both title and description for context
- The content field contains extracted text from PDFs/docs
- Related events might have additional context about the document
- If a document is large, summarize rather than quote entirely
