# Messaging archive, projects, and proactive intelligence

Status: agreed product direction; logical model with WhatsApp and Telegram archive
implementations. Projects, temporal knowledge, and proactive intelligence are
planned extensions. See WHATSAPP_INGESTION.md for the implemented boundary.
Decisions recorded on 2026-10-17. WhatsApp was the first connector; see
[WHATSAPP_INGESTION.md](WHATSAPP_INGESTION.md).

Telegram shares the archive and AI retrieval model; see
[TELEGRAM_INGESTION.md](TELEGRAM_INGESTION.md) for its official API access, setup,
supported chat types, and current capture limits.

## Purpose and boundaries

Messaging is a first-class memory source alongside documents, events, contacts,
and Digital Brain chat history. The goals are conversation recall, durable
knowledge, and proactive next steps across all sources. Preserve what was said
separately from what the system concludes. External conversations do not become
assistant chat threads or executable instructions.

This is a personal archive: retain observed message revisions and source deletion
markers. A deletion in the source app does not erase the archived content. Explicit
deletion within Digital Brain controls archive removal and invalidates dependent
indexes/knowledge. Never imply that unobserved history has been archived.

Attachment files remain in the original app. Store references and useful metadata,
not files, thumbnails, audio, transcripts, OCR, or copies in document/Immich storage.
Meaning can be extracted from the surrounding message, caption, or filename without
claiming to have inspected the attachment. A reference may outlive source access.

## Existing implementation versus planned extensions

| Existing code | Current behavior | Planned extension |
| --- | --- | --- |
| `conversations.py`, `conversation_search.py`, migrations 0071/0072 | Digital Brain chat threads, per-message lexical/semantic retrieval | Reuse retrieval patterns; external messaging has separate canonical storage and source types |
| `contacts.py`, `schemas.py:ContactIn` | Contact fields and a single `external_id` | Multiple provider/scoped identities per contact without repurposing existing integration identifiers |
| `user_facts.py`, `fact_extraction.py` | User-focused facts with ADD/UPDATE/DELETE and source thread ID; updates replace content and deletion is hard | Subject-scoped knowledge, supporting evidence, temporal revisions, and correction dependencies |
| `events.py` meeting transcript flow | Extracted action items include assignee name/email; only current-user actions become todos | One task model supporting any assignee and proposals for newly inferred work |
| `todos.py`, `todo_contacts` | Tasks with pending/completed status and associated contacts/events/places | Explicit assignee distinct from involved contacts, primary project, and evidence |
| `routes/proposals.py` | Mobile proposal route delegates to contact-enrichment proposals | Typed proposed items for tasks, projects, and actions, with durable resolution history |
| `daily_briefing_jobs.py`, `async_jobs` | Existing scheduled jobs and briefing generation | Independent incremental extraction and daily cross-source analysis jobs; briefing consumes their results |

Do not silently replace existing meeting todo behavior or existing manual contact
confirmation flows. Integration of those paths is an explicit implementation step.

## Logical entities

Names below express domain boundaries, not final table names. Every record and
cross-reference is owner-scoped, even in the current single-user installation.

| Entity | Meaning and minimum information |
| --- | --- |
| External account | Provider account, owner, optional workspace/tenant, authenticated self identity; separate from an ephemeral connector session |
| External identity | Person, bot, organization, or unknown actor; provider-scoped identifiers, observed names/handles, optional contact association |
| Identity identifier | Namespace/value, provider scope, observation provenance, and validity when known; permits multiple evidenced aliases for one actor |
| Contact association | At most one active contact per identity, with manual/inferred origin, evidence, and correction history; one contact can have many identities |
| External conversation | DM/group/channel/container, source identifier and scope, title, optional parent container, classification and analysis defaults |
| Conversation membership | External identity observed in a conversation, optional joined/left times, observation source; membership does not imply reading |
| Message | Source key, conversation, sender identity, original text/caption, direction, sent/observed times, optional reply and thread-root references |
| Message revision | Observed edit/deletion state, source revision/order if available, observation time, and content hash; retain older versions |
| Attachment reference | Parent message, source attachment identifier if available, ordinal/type, filename/size when supplied, safe source locator, availability |
| Project | Intended outcome, context, optional area, lifecycle status, optional target date, and linked records |
| Area | Optional ongoing responsibility without a completion date, such as Health or Home |
| Knowledge item and revision | Subject(s), attributed statement or supported fact, certainty, evidence, applicable time, learned time, and supersession/conflict relationships |
| Task | Description, explicit assignee or unresolved assignee identity, status, optional due date, primary project, related contacts, and evidence |
| Proposed item | Typed suggested task/project/action, supporting evidence, proposed payload, and acceptance/dismissal/supersession history |
| Evidence association | Derived record and specific source record/revision, derivation version, and affected dependency |

Source keys must be scoped according to each provider's guarantees. Preserve both
original wire identifiers and evidenced canonical aliases; display names are not
keys. Import connections can observe the same external entity without creating
duplicate canonical people/messages. Do not globally merge accounts or workspaces.

Preserve reply and thread-root references even if the target is not archived yet.
Distinguish quoted/forwarded authorship from the person who sent the message.
Keep source timestamps separate from ingestion timestamps and represent unknown
timezone/precision explicitly rather than inventing precision.

Membership records explain who was present independently of who spoke. For
example, Pedro can belong to a group without sending any archived messages. A
known later join date prevents assuming he was present for an earlier exchange.
Absent membership history remains unknown; present membership does not establish
past membership.

## Contact linking and contact knowledge

One contact can have multiple WhatsApp, Telegram, and workspace-specific Slack
identities. Channel membership does not create another identity for the person.
Names alone never establish a confirmed match. Identifier matches can produce
explainable suggestions; ambiguous identities remain unlinked.

The mobile External identities screen is reachable from Contacts and supports
Unlinked, Suggested matches, and Linked views. Identity details show provider,
workspace/account context, available identifiers, and relevant exchange context.
Actions: Attach to contact, Create contact, Leave unlinked, confirm a suggestion,
and correct/remove a link. Contact details expose linked identities and an Attach
identity action. Follow the existing contact picker, keyboard, and header patterns.

Both extraction rhythms update relevant contact knowledge automatically:

| Evidence | Result |
| --- | --- |
| "Here is my new number" | Evidenced phone update with prior value/provenance retained |
| "I joined Acme as finance director" | Current employment with previous employment preserved |
| "I'm vegetarian" | Durable preference |
| "I'm away until October 25" | Temporary availability with an end date |
| "I'm coordinating the renovation" | Project-specific role |
| "I'll send the quote Friday" | Proposed task assigned to that contact |

Keep organized knowledge, not an ever-growing appended notes field. Contact views,
search, and project reasoning read the same knowledge. Repeated evidence supports
an existing item. Material changes create a revision. Hypotheses retain their
uncertainty; "may change jobs" does not replace an employer. Explicit user
corrections take precedence over automatic updates. Recent updates can appear on
the contact screen with source links. Do not insert engineering caveats into UI copy.

## Context, areas, projects, and topics

Context values are Work, Personal, Mixed, and Unclassified. They are distinct from
project/area associations and topic labels such as contracts or travel.

Default precedence: explicit message/exchange assignment, then conversation,
then account/workspace. No assignment means Unclassified. Mixed is a deliberate
classification, not missing information. Store manual/inherited/inferred origin;
manual assignments outrank inference. The same contact may participate in both
work and personal exchanges and does not determine their context.

A company workspace can default to Work; a personal WhatsApp account can be Mixed,
with Family classified Personal and a client group classified Work. An exchange
inside a mixed DM can be assigned separately. Derived knowledge/tasks inherit
supporting context, retaining Mixed/Unclassified when evidence does not settle it.

An area is an ongoing responsibility; a project has an intended outcome. Neither
is mandatory. A project links conversations/exchanges, documents and attachment
references, events, people/roles, tasks, facts, decisions, and open questions.
Associations can be many-to-many. Each task has one optional primary project and
may reference others. Linking a whole conversation supplies a default, not proof
that every exchange concerns that project or every member owns its tasks.

Project suggestions enter Proposed items. User-created projects need no proposal.
Archive/search inclusion and proactive monitoring are independent controls, with
account/conversation defaults and project-level settings. Resolve conflicts with
explicit source monitoring exclusions taking precedence over project inclusion.
The exact monitoring choices/defaults remain an implementation design decision.

## Facts, time, uncertainty, and corrections

Automatic facts retain attribution and evidence. "Ana says delivery is Friday"
can be learned automatically without proving that delivery will occur. Never
convert archived third-party text into a user instruction or a hard behavioral rule.

Examples:

- January: "I live in Lisbon." June: "I moved to Porto last week." Porto becomes
  current; Lisbon remains historical. The move date and June observation date are
  distinct. Do not invent an exact move date from an approximate statement.
- Monday's EUR 8,000 quote followed by an explicitly revised EUR 9,200 quote is
  supersession, preserving both amounts and their evidence.
- Ana says installation is included; Pedro says it is extra. Keep an unresolved
  conflict, rather than letting the last ingested statement win. Clarification can
  become a proposed action.
- "I might move" is a possible plan. "I decided to stay" resolves that plan;
  neither statement establishes a changed current address.

Store time of applicability separately from time learned, plus status such as
current/historical/uncertain/conflicted. Unknown boundaries remain unknown. A
source correction or contact reassignment queues affected derivations for review.
For example, relinking a Telegram identity from Ana to Beatriz must reconsider a
fact attributed to Ana from that identity. Preserve original source identity and
manual edits; flag accepted user-edited work where silent rewriting would be wrong.

## Tasks and proposed items

Use one task model, including work assigned to other people. Assignee is distinct
from involved contacts: "Send Ana the quote" can be assigned to the user. Unknown
assignment remains unresolved rather than guessed. No separate commitment entity
is required; source evidence can establish that someone promised the task.

New inferred tasks, projects, and external actions enter Proposed items. Acceptance
is required before activating that proposed work or executing its action. Automatic
facts/contact knowledge do not require this review, while ambiguity and explicit
user corrections remain respected. Acceptance must validate current evidence and
permissions and be idempotent; an expired or invalid action is not replayed.

Before adding anything, retrieve similar existing facts, tasks, projects, and
proposals, including completed tasks and dismissed proposals. Decide whether to
add evidence, revise an item, or create a distinct one. Similarity finds candidates;
it is not an identity guarantee. Source-based idempotence separately prevents
reprocessing the same evidence into duplicates. New evidence can justify revisiting
a dismissal, but unchanged evidence must not repeatedly recreate it.

## Analysis and retrieval

1. Incremental extraction runs in debounced, bounded batches after material arrives
   or changes: indexing, identity resolution, facts/contact updates, and candidate
   task/project/action discovery using related retrieved context.
2. A daily cross-source review considers changes since its last successful
   checkpoint, open tasks, unresolved questions, approaching dates, and relevant
   historical evidence across messages, events, documents, contacts, and projects.
   It updates knowledge and produces deduplicated proposed items. Run independently
   of daily briefing generation; the briefing can present its useful results.

Do not feed the entire archive into a model each day. Persist run/checkpoint and
evidence revision information, bound retrieval and inference, and recover missed
runs without repeating effects. Late-arriving old messages count as new observations
but do not automatically supersede newer applicable facts. No new schedule/timezone
has been selected yet; daily is the agreed cadence.

Messages are canonical storage; contextual exchanges are derived retrieval units.
Keep ordered source-message/revision references in passages and summaries, preserving
speaker/date provenance and owner scope. A search hit expands to bounded surrounding
context. Summaries and embeddings are rebuildable and never replace source evidence.
Source locators are structured references first, deep links only when verified;
otherwise show conversation, sender, time, and available filename.

"No matching delivery message was found" is not "the delivery never happened."
Coverage, missing history, event loss, and attachment availability must remain
available to reasoning. Content belongs in private storage, not application logs,
analytics, Mirador spans, metrics labels, or general diagnostic exports.

## Next design work

Before implementation, finalize migrations/API contracts for ownership, evidence
references, revisions, task assignment, typed proposals, and project associations.
Choose monitoring defaults, classification UI, intelligence budgets/schedule, and
archive purge/backup behavior. These are remaining implementation details, not
reasons to reopen the product decisions above.
