"""Supplement GBrain's deliberate no-reply exclusion using its existing OAuth.

No upstream files are modified. A small Bun bridge imports the pinned GBrain
Google client and token provider. Tokens stay inside that child process/vault;
only bounded, normalized email records cross back to Python.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parseaddr
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

import yaml

from .gbrain import GBrain, GBrainError


_THREAD_HEADER = re.compile(
    r"^## (?:→ )?.+ · \d{4}-\d{2}-\d{2} \d{2}:\d{2}\s*$", re.MULTILINE
)
_MESSAGE_ID = re.compile(r"[A-Za-z0-9]{10,60}\Z")


def _body(page: dict[str, Any]) -> str:
    if "compiled_truth" in page:
        return page["compiled_truth"] or ""
    content = page.get("content", page.get("body", ""))
    if content.startswith("---\n"):
        parts = content.split("\n---", 1)
        if len(parts) == 2:
            return parts[1].strip()
    return content


def _newest_message(body: str) -> str:
    headers = list(_THREAD_HEADER.finditer(body))
    if headers:
        body = body[headers[-1].end():].lstrip()
        # The renderer puts citation and To/Cc routing above each message.
        body = re.sub(r"^\[Source:.*?\]\s*\n", "", body, count=1)
        body = re.sub(r"^To: [^\n]*\n", "", body.lstrip(), count=1)
    # Avoid feeding quoted historical commitments back as a new observation.
    body = re.split(r"\nOn [^\n]{0,200}wrote:|\n--\s*\n", body, maxsplit=1)[0]
    return "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith(">")
    ).strip()[:16_000]


def normalize_gbrain_event(page: dict[str, Any]) -> dict[str, Any]:
    """Return ``{source, payload, event_id}`` for Runtime.submit.

    Native Gmail pages contain entire threads: only the newest message is the
    observation. Message-ID identity prevents label updates and supplemental
    intake from delivering that same observation twice.
    """
    fm = page.get("frontmatter") or {}
    slug = page.get("slug", "")
    source_id = page.get("source_id", "default")
    title = page.get("title", fm.get("title", ""))
    account_hash = hashlib.sha256(str(fm.get("account", "")).encode()).hexdigest()[:12]
    payload = {
        "title": title, "slug": slug, "source_id": source_id,
        "page_revision": page.get("revision"), "frontmatter": fm,
    }
    if slug.startswith("calendar/") or page.get("type") == "meeting":
        for key in ("start", "end", "all_day", "event_id", "organizer", "attendees", "location", "url"):
            if key in fm:
                payload[key] = fm[key]
        payload["text"] = _body(page)
        identity = fm.get("event_id", slug)
        revision = page.get("revision", page.get("updated_at", ""))
        event_id = f"google-calendar:{account_hash}:{identity}:{revision}"
        return {"source": "calendar", "payload": payload, "event_id": event_id}
    if slug.startswith(("emails/", "events/gmail/")) or page.get("type") == "email":
        for key in ("from", "to", "cc", "date", "thread_id", "message_id", "labels", "url"):
            if key in fm:
                payload[key] = fm[key]
        payload["subject"] = title
        payload["from_address"] = parseaddr(str(fm.get("from", "")))[1].lower()
        payload["received_at"] = fm.get("date")
        payload["text"] = _newest_message(_body(page))
        identity = fm.get("message_id") or f"{source_id}:{slug}:{page.get('revision', '')}"
        event_id = f"gmail:{account_hash}:{identity}"
        return {"source": "email", "payload": payload, "event_id": event_id}
    raise ValueError("Page is neither a Gmail message nor a Calendar event")


# Imports are from the user's installed, pinned upstream checkout. The helper
# never prints access/refresh tokens and makes only read-only Gmail requests.
_BRIDGE = r'''
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
const root = Bun.argv[2];
const input = JSON.parse(Bun.argv[3]);
const mod = async (path) => import(pathToFileURL(join(root, path)).href);
try {
  const { openVault } = await mod('src/core/creds/vault.ts');
  const { GoogleTokenProvider, GOOGLE_SERVICE_SCOPES } = await mod('src/core/creds/providers/google.ts');
  const { GmailClient } = await mod('src/core/google/google-clients.ts');
  const { isNoiseSender } = await mod('src/core/google/google-render.ts');
  const vault = openVault();
  const accounts = (await vault.list({ provider: 'google' })).filter(
    a => a.scopes?.includes(GOOGLE_SERVICE_SCOPES.gmail)
  );
  if (accounts.length !== 1) throw { code: 'google_account_ambiguous_or_missing' };
  const account = accounts[0];
  const client = new GmailClient(new GoogleTokenProvider(vault, account.id));
  const params = new URLSearchParams({ maxResults: String(input.limit), q: input.query });
  if (input.page_token) params.set('pageToken', input.page_token);
  const response = await client.fetchJSON(
    'https://gmail.googleapis.com/gmail/v1/users/me/messages?' + params,
    'gmail', { retries: 1, rateLimitRetries: 1 }
  );
  const listed = (response.messages ?? []).slice(0, input.limit);
  const wanted = new Set(listed.map(m => m.id));
  const tids = [...new Set(listed.map(m => m.threadId))];
  const messages = [];
  for (let i = 0; i < tids.length; i += 3) {
    const threads = await Promise.all(tids.slice(i, i + 3).map(
      tid => client.getThread(tid, account.account, { bodyCapChars: 8000 })
    ));
    for (const thread of threads) {
      // GBrain retains a thread with any human author, so don't duplicate it.
      if (!thread.messages.length || !thread.messages.every(m => isNoiseSender(m.fromAddress))) continue;
      for (const m of thread.messages) {
        if (!wanted.has(m.id)) continue;
        messages.push({ ...m, account: account.account });
      }
    }
  }
  console.log(JSON.stringify({ messages, scanned: listed.length,
    next_page_token: response.nextPageToken ?? null }));
} catch (error) {
  const code = typeof error?.code === 'string' && /^[a-z_]+$/.test(error.code)
    ? error.code : 'google_intake_failed';
  console.log(JSON.stringify({ error: code }));
  process.exitCode = 1;
}
'''


class GoogleAutomatedIntake:
    """Bounded catch-up for automated email that native GBrain discards.

    Each poll scans at most ``limit`` Gmail message IDs from a fixed 24h query.
    The private continuation advances only after every page write commits.
    Existing GBrain pages and stable request UUIDs make replay restart-safe.
    Salience is left to Jev; a sender heuristic cannot distinguish a booking
    confirmation from marketing sent by the same service.
    """

    def __init__(
        self, brain: GBrain, checkout: str | Path | None = None, limit: int = 25,
    ) -> None:
        if not 1 <= limit <= 25:
            raise ValueError("intake limit must be between 1 and 25")
        self.brain = brain
        if checkout is None:
            entry = Path(brain.command[-1])
            if entry.name != "cli.ts":
                raise ValueError("Provide the pinned GBrain checkout for intake")
            checkout = entry.parent.parent
        self.checkout = Path(checkout).expanduser().resolve()
        self.limit = limit
        self.state_path = brain.home / ".memento-google-intake.json"

    def _state(self) -> dict[str, Any]:
        try:
            state = json.loads(self.state_path.read_text())
            # Do not continue a stale 24h snapshot indefinitely after downtime.
            if state.get("page_token") and state.get("started_at", 0) > datetime.now(timezone.utc).timestamp() - 86400:
                return state
        except (OSError, ValueError):
            pass
        now = int(datetime.now(timezone.utc).timestamp())
        senders = " ".join(f"from:{word}" for word in (
            "noreply", "no-reply", "notifications", "notification",
            "calendar-notification", "mailer-daemon", "postmaster",
            "donotreply", "do-not-reply",
        ))
        return {"query": f"after:{now - 86400} before:{now + 1} -in:spam -in:trash {{{senders}}}",
                "page_token": None, "started_at": now}

    async def _fetch(self, state: dict[str, Any]) -> dict[str, Any]:
        self.brain.home.mkdir(parents=True, exist_ok=True)
        helper = self.brain.home / ".memento-google-intake.ts"
        # Helper contains code only, never account content or credentials.
        helper.write_text(_BRIDGE)
        helper.chmod(0o600)
        bridge = GBrain(
            [self.brain.command[0], str(helper)], self.brain.home,
            timeout=self.brain.timeout,
        )
        async with bridge._serialized():
            return await bridge._execute([
                str(self.checkout), json.dumps({**state, "limit": self.limit}),
            ])

    @staticmethod
    def _markdown(message: dict[str, Any]) -> tuple[str, str]:
        message_id = str(message["id"])
        if not _MESSAGE_ID.fullmatch(message_id):
            raise ValueError("Invalid Gmail message id")
        account = str(message["account"])
        frontmatter = {
            "type": "email", "title": message.get("subject", "(no subject)"),
            "intake": "memento-google-automated", "account": account,
            "message_id": message_id, "thread_id": message["threadId"],
            "from": message.get("from", ""), "to": message.get("to", []),
            "cc": message.get("cc", []), "date": message["dateIso"],
            "labels": message.get("labelIds", []),
            "list_unsubscribe": bool(message.get("listUnsubscribe")),
            "url": f"https://mail.google.com/mail/u/?authuser={quote(account)}#all/{message_id}",
        }
        markdown = "---\n" + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
        markdown += "---\n\n" + str(message.get("bodyText", "")).strip() + "\n"
        return f"events/gmail/{message_id}", markdown

    async def poll(self) -> list[dict[str, Any]]:
        state = self._state()
        result = await self._fetch(state)
        existing = {
            p["slug"] for p in await self.brain.list_pages(source=self.brain.source)
            if p["slug"].startswith("events/gmail/")
        }
        envelopes = []
        for message in result["messages"]:
            slug, markdown = self._markdown(message)
            # Include tombstones: deleting an intercepted message opts it out.
            if slug in existing:
                continue
            intent = f"memento:{self.brain.home}:{slug}:{hashlib.sha256(markdown.encode()).hexdigest()}"
            request_id = str(uuid5(NAMESPACE_URL, intent))
            receipt = await self.brain.put_page(slug, markdown, request_id=request_id)
            if receipt.get("state") != "committed":
                raise GBrainError("automated_intake", "write_not_committed", request_id=request_id)
            page = await self.brain.get_page(slug)
            envelopes.append(normalize_gbrain_event(page))
            existing.add(slug)
        state["page_token"] = result.get("next_page_token")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state))
        temporary.chmod(0o600)
        temporary.replace(self.state_path)
        return envelopes
