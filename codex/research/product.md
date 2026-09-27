# Memento: memories that follow through

**Tell your AI once. It keeps track and comes back when the information matters.**

Memento turns a remembered plan, promise, or interest into a small ongoing
responsibility. The Markdown body describes what is known. A Python recipe in
frontmatter describes when that knowledge should become useful again. A memory
without a recipe remains useful knowledge; it simply has no background watch.
**The memory content is primary; proactive behavior is optional.** Preferences,
context, and durable facts should remain normal memories that an assistant can
retrieve even when there is nothing to schedule or monitor.

The intended experience lives alongside the user's existing assistant and
memory system. The current web interface exposes memories, decisions, recipes,
and delivery receipts, and supplies test events. It is the demo's observation
and debugging surface. Integration into a particular assistant's conversation
requires its own adapter; no general plugin or ChatGPT integration is claimed.

## Who creates the memory and its behavior?

The same agent creates both. In this prototype, Gmail and Calendar become
source pages in unmodified GBrain. Dashboard chat provides another source.
Jev decides whether a new observation contains something worth remembering.
River then writes the body, provenance, and recipe through PydanticAI. The
recipe must compile and pass validation before the memory is stored.

The user does not need to write Python. They can inspect the generated recipe
alongside its source and edit the Markdown. Other agents could author the same
format through GBrain's ordinary page interface; automatic integration with
every existing assistant is not part of this demo.

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
