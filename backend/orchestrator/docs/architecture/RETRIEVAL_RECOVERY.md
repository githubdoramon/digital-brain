# Retrieval, research and completion recovery

Routing is a strategy hint. General conversation uses the fallback route and can
answer directly when the request needs no retrieval or action. The retired
`conversational` label never exempts a request from completion checks, including
when returned by an older model. Core memory, contact reads and structured reads
remain visible in normal chat; existing voice and execution restrictions apply.

## Source prerequisites

For personal meeting preparation involving participants, the controller tracks:

1. Identify one matching stored event in the requested date window.
2. Inspect its actual participant junction links and stored contact emails.
3. For public research, attempt identity-specific searches for each participant,
   excluding the account owner's own contact, and cover each person in the answer.

Company team pages and topic-matched contacts do not establish attendance. Multiple
matching events require clarification. An explicit attendee count cannot be met by
an incomplete list. Public search may return no reliable profile: preserve that
uncertainty for that attendee rather than inventing a biography. Failed or empty
identity-specific research is an attempt, not verified profile evidence.

The controller blocks external research before participant discovery, returning
concrete read guidance. Short follow-ups recover the most recent relevant user
request from the last twelve dialogue messages; assistant assertions never define
the task. Explicit task replacement stops this carryover. If originating message
`sent_at`/`created_at` and timezone metadata are supplied, relative dates use them;
otherwise the trusted current request clock is used. Original stored messages are
not rewritten. Temporal recovery also considers the goal when the model omits
dates from its search query. Today/yesterday/tomorrow and calendar week windows
are resolved deterministically in the request timezone.

These deterministic prerequisites currently cover meeting participant research.
Other requests continue through shared goal verification and tool recovery.
A failed final verification cannot become a successful answer merely because the
turn budget ran out; the controller returns an incomplete outcome.

## Search evidence

Event discovery combines semantic/full-text retrieval with literal topic matches
in event titles. Generic query words such as meeting and participants do not mask
a distinctive title term. Structured time/contact/tag filters still constrain the
candidate set. Search result counts describe candidates, not proven relevance.

`search_memories` includes `knowledge` alongside event/document/conversation/message
sources. Knowledge uses `knowledge.list_items` with authenticated owner and exact
subject references, respecting deletion and canonical identity links. Results retain
uncertainty, inference, revisions, applicability dates and original source evidence,
including through tool-result compaction. Tags exclude knowledge because knowledge
has no corresponding verified tag association. A fact's validity dates remain
separate from the dates of its underlying evidence; source selection and dates must
be inspected before applying it to a particular event.

## Recovery boundaries

Every rejected call counts toward the existing call/repair limits. Read-only
representation repairs are revalidated; no tool names, identities, unknown
parameters or action authorization are guessed. Visibility may widen once when a
registered requested/recommended tool is hidden, final verification identifies
missing evidence, or repeated attempts fail or make no progress. The same policy
is used by streaming and non-streaming chat. Info cards cannot suspend unfinished
retrieval; interactive clarification still can after stored event discovery.

Regression coverage includes both controller loops with a scripted model,
malformed arguments, irrelevant/wrong-date events, five attendees, ambiguity,
continuations, premature UI completion, and owner-scoped PostgreSQL knowledge and
participant retrieval. Scripted-model tests do not establish live-model behavior.
