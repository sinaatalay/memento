# Memento: memories that follow through

**Tell your AI once. It keeps track and comes back when the information matters.**

Memento turns a remembered plan, promise, or interest into a small ongoing
responsibility. The Markdown body describes what is known. A Python recipe in
frontmatter describes when that knowledge should become useful again. A memory
without a recipe remains useful knowledge; it simply has no background watch.
**The memory content is primary; proactive behavior is optional.** Preferences,
context, and durable facts should remain normal memories that an assistant can
retrieve even when there is nothing to schedule or monitor.

The product is a **proactivity extension for GBrain**, packaged as a local daemon
plus an agent authoring skill. Keep talking to the assistant you already use.
Keep its normal GBrain pages. Add optional behavior to those same pages.
The web interface is a demo inspector showing recipes, decisions, and delivery
receipts. `memento watch --gbrain --google` runs without that interface.

## Who creates the memory and its behavior?

There are two paths, with different responsibilities:

1. **An existing agent or person writes a memory.** It is already an intentional
   memory; Memento does not ask Jev whether it deserves to exist. An ordinary
   default-source page at any safe slug can be passive or carry a recipe. A file
   edit or GBrain `put_page` changes that same page, without making another copy.
   The [authoring skill](../skills/memento/SKILL.md) teaches the existing agent
   how to add behavior while preserving the page's content and metadata.
2. **Optional automatic capture from incoming events.** Gmail, Calendar, or
   explicitly forwarded chat can contain new information. Jev scores whether
   the event contains an affirmed fact, booking, commitment, deadline, or request
   to remember. At the configured threshold (currently .8), River writes a
   sourced memory and recipe through PydanticAI. This is an ingestion adapter,
   not the definition of a Memento memory.

The user does not need to write Python. The agent saving the memory can also
write the recipe; River is not its mandatory author. Every recipe is compiled
and validated before it is activated. River is required when a recipe chooses
the generative `rewrite(this)` action. Timed reminders do not need an LLM call
when they fire. Unknown front matter such as tags, type, and custom fields is
preserved during Memento rewrites.

GBrain has both Markdown pages and structured fact rows. This implementation
extends pages. It does not claim to attach recipes to every standalone fact
created by GBrain's `remember`/`extract_facts` operations. That would need an
explicit fact-to-page or fact-recipe bridge.

## Existing assistants, rather than a new chat destination

GBrain recommends [adding memory to an existing coding agent](https://github.com/garrytan/gbrain/blob/master/docs/tutorials/connect-coding-agent.md).
Its Claude Code and Codex plugins package MCP tools and skills. The same
`get_page`/`put_page` interface used by those agents is used by Memento's shared
HTTP adapter. One GBrain server owns local PGLite; the daemon and agent clients
share it. Running separate permanent stdio owners against that database fails.

Calling Memento a "GBrain extension" describes the product well. The verified
packaging is an adjacent daemon plus skill, not a registered generic plugin
inside GBrain. Native GBrain job handlers are another extension path, but this
demo does not run the Python engine inside that worker.

GBrain [documents a ChatGPT connection](https://github.com/garrytan/gbrain/blob/master/docs/mcp/CHATGPT.md)
through public HTTPS MCP and OAuth/PKCE. Our demo stays local. We have verified
MCP transport and canonical page sharing, not a live ChatGPT session. Attaching
MCP also does not automatically stream every chat utterance to Memento.
Ordinary recall belongs to the existing assistant; `surface(this)` needs an
explicit chat adapter and is not the headline product claim.

## What distinguishes this from existing memory?

"Ask about breakfast; the assistant remembers your flight" is ordinary recall.
Keep it as a compatibility check, not the novel demo. The stronger sequence is:
save a normal memory, stop chatting, receive a relevant external event or reach
a deadline, and get an unsolicited, justified notification tied to that memory.

GBrain itself already has [open-loop tracking](https://github.com/garrytan/gbrain/blob/master/docs/guides/open-loops.md),
commitment extraction, and scheduled background jobs. It would be inaccurate to
describe the whole ecosystem as passive. Memento's design contribution is a
portable, inspectable behavior recipe attached to each ordinary memory:
specific semantic conditions, deterministic scheduling, and a persisted action
lifecycle. That is the hypothesis the demo tests; it is not a claim that no
other product can send proactive reminders.

## What runs continuously?

The runtime registers watches and scheduled reminders. It does not repeatedly
ask a general-purpose agent to reconsider every memory.

- **Deterministic:** date arithmetic, source filters, clock deadlines, expiry,
  recipe validation, revision checks, event identities, and notification state.
- **Semantic:** Jev interprets an incoming event against the applicable watches
  in one typed request. Probabilities and thresholds are visible. This matching
  remains probabilistic; it is not a deterministic guarantee of meaning.
- **Generative:** River writes or updates a memory after relevant evidence
  arrives. The new recipe is checked before replacing the previous behavior.

Recipes can currently observe time, incoming email, Calendar events, and
dashboard chat. A watch embeds the relevant remembered facts in its question.
Arbitrary website monitoring, general Python network access, and unrestricted
agent tool execution are outside the implemented DSL. Chat is a generic event
source, currently supplied by the local API and demo composer; connecting an
installed assistant to that source is a separate integration step.

## What happens when something matters?

| Recipe operation | User-visible behavior |
| --- | --- |
| `remind(when, text)` | Email the owner when a deterministic deadline arrives. |
| `on(source, when=question, do=alert(text))` | Email the owner when an incoming event satisfies a watch. |
| `surface(this)` | Bring the remembered context into the dashboard conversation. |
| `rewrite(this)` | Update the body and recipe using the new evidence. |
| `expires(when)` | Stop the memory's active behavior after its useful lifetime. |
| **Stop watching** in the dashboard | Disable the behavior and cancel pending notifications while retaining the memory. |

Changing a recipe replaces its old behavior; stale asynchronous rewrites cannot
overwrite a newer version. Deleting a GBrain memory stops its local watch.
Completion or cancellation can produce a passive memory with an empty recipe.
Memento sends only to the configured connected Gmail account. It does not send
customer follow-ups or introductions to other people.

## Three useful cases beyond flights

These scenarios use implemented DSL capabilities. They are product examples,
not claims that these particular customer, promise, or recruiting workflows
were tested against real personal accounts.

| Remembered situation | What the recipe watches | Useful intervention |
| --- | --- | --- |
| “Acme can start the pilot when SSO ships. I promised Maya an update.” | `on(email, when=noul(...), do=[alert(...), rewrite(this)])` distinguishes an actual release from a plan. | The blocker clears; Memento brings back the customer commitment and updates its status. [Example recipe](../examples/customer-followup.md). |
| “I owe the proposal Friday, but need the supplier's estimate first.” | `remind(deadline - hours(24), ...)` plus an email watch for the missing estimate and a chat watch for completion. | It reminds the owner of the deadline, then notices when the missing input arrives. “I sent it” can rewrite the memory and retire the remaining reminder. |
| “Priya needs a founding designer.” | Email and chat watches ask whether a specific suitable designer has become available. | Memento suggests a possible introduction or surfaces the hiring context when that person comes up. The owner chooses whether to contact anyone. [Example recipe](../examples/designer-introduction.md). |

The shared benefit is remembering the consequence of new information: a shipped
feature resolves an old dependency; an incoming estimate makes a promise
actionable; someone becoming available connects to an earlier request.

## What the demo establishes

The primary demo now starts with a page authored by a separate agent using the
Memento skill and written by an independent MCP client at `projects/demo/...`.
It first registers with no recipe. Adding the recipe activates the same page.
A synthetic roadmap email causes no alert or rewrite; a synthetic confirmed
SSO release triggers one real owner email and a same-page update in **12.17s**.
The updated page keeps its type, tags, custom metadata, pricing context, and
unfinished promise to Maya, while retiring the fulfilled shipment watch. The
email was independently verified in Gmail's Sent and Inbox. No chat turn was
part of this sequence.

A separate direct-file test created `notes/demo/...` in the canonical memory
folder, added a future reminder recipe, and removed it again. Working-tree sync
observed all three forms; the body and custom metadata survived; removing the
recipe stopped the watch. No event capture or external notification was needed.
Both tests exercise first-class pages outside Memento's own creation path.

The complete synthetic flight sequence uses real Jev and River calls: create a
memory, reject an unrelated event, surface context in chat, recognize a schedule
change, rewrite the memory, and deliver a due reminder. Synthetic events and the
accelerated clock are visibly labeled and isolated from real memories.

Separately, real Gmail and Calendar ingestion, a real Calendar-to-memory write,
an automated-email gate rejection, Gmail notification delivery, and feedback
suppression were verified against the connected account. See the measured
[GBrain/integration results](gbrain.md) and [provider experiments](providers.md).

Everything runs locally. The background process and computer must remain
running for monitoring and delivery. The dashboard shows memories, recipes,
decisions, and outcomes; Gmail reaches the user's inbox. This is a functioning
local prototype, with the recipe format as the portable connection between
remembered knowledge and future behavior.

The current shared-server demo includes a small compatibility patch for the
pinned GBrain release's native Google sync dispatch. It is tested in a separate
checkout; the original stays clean. See [the exact patch and evidence](../compat/README.md).
This is an integration finding, not a claim of a zero-configuration plugin
installation across all assistant products.
