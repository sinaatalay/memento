"""Self-email notifications using the existing GBrain Google OAuth vault."""
from __future__ import annotations

import base64
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate, getaddresses, parseaddr
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable
from uuid import uuid4

from .gbrain import GBrain, GBrainError


def is_memento_notification(
    payload: dict[str, Any], own_address: str | None = None,
    sent_message_ids: Iterable[str] = (),
) -> bool:
    """Identify our own output without treating human replies as notifications."""
    fm = payload.get("frontmatter") or {}
    message_id = str(payload.get("message_id", fm.get("message_id", "")))
    if message_id and message_id in set(sent_message_ids):
        return True
    raw_headers = payload.get("headers", fm.get("headers", {}))
    if isinstance(raw_headers, list):
        headers = {h["name"].lower(): h["value"] for h in raw_headers}
    else:
        headers = {str(k).lower(): str(v) for k, v in raw_headers.items()}
    own = (own_address or fm.get("account") or payload.get("account") or "").lower()
    sender = parseaddr(str(payload.get("from", fm.get("from", headers.get("from", "")))))[1].lower()
    to = payload.get("to", fm.get("to", headers.get("to", [])))
    addresses = [to] if isinstance(to, str) else list(to or [])
    recipients = {address.lower() for _, address in getaddresses(addresses)}
    if not own or sender != own or own not in recipients:
        return False
    if headers.get("x-memento-notification"):
        return True
    if headers.get("message-id", "").startswith("<memento.") and headers.get("message-id", "").endswith("@memento.local>"):
        return True
    # A native thread title is its FIRST subject. Only use a proven latest
    # subject or a directly supplied message subject, never an inherited title.
    if payload.get("subject_is_latest") is False:
        return False
    subject = str(payload.get("subject", headers.get("subject", "")))
    return subject.startswith("[Memento] ")


def build_notification(
    recipient: str, text: str, *, subject: str, notification_id: str,
) -> tuple[str, str, str]:
    """Build RFC MIME; user text never participates in routing or headers."""
    if parseaddr(recipient)[1] != recipient or not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", recipient):
        raise ValueError("A single configured email recipient is required")
    if "\r" in subject or "\n" in subject:
        raise ValueError("Email subject must be one line")
    tag = hashlib.sha256(notification_id.encode()).hexdigest()
    message_id = f"<memento.{tag}@memento.local>"
    subject = subject if subject.startswith("[Memento] ") else f"[Memento] {subject}"
    message = EmailMessage(policy=SMTP)
    message["From"] = f"Memento <{recipient}>"
    message["To"] = recipient
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False, usegmt=True)
    message["Message-ID"] = message_id
    message["X-Memento-Notification"] = tag
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(text)
    intent = hashlib.sha256(json.dumps([recipient, subject, text]).encode()).hexdigest()
    return base64.urlsafe_b64encode(message.as_bytes()).decode(), message_id, intent


_BRIDGE = r'''
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
const root = Bun.argv[2];
const mode = Bun.argv[3];
const input = JSON.parse(await Bun.stdin.text());
const mod = async (p) => import(pathToFileURL(join(root, p)).href);
try {
  const { openVault, credentialId } = await mod('src/core/creds/vault.ts');
  const { GoogleTokenProvider } = await mod('src/core/creds/providers/google.ts');
  const { GmailClient } = await mod('src/core/google/google-clients.ts');
  const vault = openVault();
  const entry = await vault.get(credentialId('google', input.recipient));
  if (!entry) throw {code:'google_account_missing'};
  if (!entry.meta.scopes?.includes('https://www.googleapis.com/auth/gmail.send')) throw {code:'scope_missing_gmail_send'};
  const tokens = new GoogleTokenProvider(vault, entry.id);
  const client = new GmailClient(tokens);
  const profile = await client.getProfile();
  if (profile.emailAddress.toLowerCase() !== input.recipient.toLowerCase()) throw {code:'recipient_account_mismatch'};
  if (mode === 'connect') {
    console.log(JSON.stringify({connected:true,recipient_matches:true,send_scope:true}));
  } else if (mode === 'find') {
    const q = 'in:sent rfc822msgid:' + input.rfc_message_id.replace(/[<>]/g, '');
    const found = await client.fetchJSON('https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=5&q='+encodeURIComponent(q),'gmail');
    console.log(JSON.stringify({message_id:found.messages?.[0]?.id ?? null}));
  } else if (mode === 'send') {
    let response;
    for (let attempt=0; attempt<2; attempt++) {
      response = await fetch('https://gmail.googleapis.com/gmail/v1/users/me/messages/send', {
        method:'POST', headers:{authorization:'Bearer '+await tokens.getAccessToken(),'content-type':'application/json'},
        body:JSON.stringify({raw:input.raw})
      });
      if (response.status !== 401 || attempt) break;
      await tokens.forceRefresh();
    }
    if (!response.ok) throw {code:'gmail_send_http_'+response.status};
    const message = await response.json();
    if (!message.id) throw {code:'gmail_send_missing_receipt'};
    console.log(JSON.stringify({message_id:message.id}));
  } else if (mode === 'verify') {
    const message = await client.fetchJSON('https://gmail.googleapis.com/gmail/v1/users/me/messages/'+encodeURIComponent(input.message_id)+'?format=metadata','gmail');
    const headers = Object.fromEntries((message.payload?.headers ?? []).map(h => [h.name.toLowerCase(),h.value]));
    console.log(JSON.stringify({sent:message.labelIds?.includes('SENT') ?? false,
      received:message.labelIds?.includes('INBOX') ?? false,
      marker:Boolean(headers['x-memento-notification']),auto_generated:headers['auto-submitted']==='auto-generated',
      self_address_matches:headers.to?.toLowerCase()===input.recipient.toLowerCase()}));
  } else throw {code:'unknown_email_operation'};
} catch(error) {
  const code = typeof error?.code === 'string' && /^[a-z_0-9]+$/.test(error.code) ? error.code : 'email_notification_failed';
  console.log(JSON.stringify({error:code})); process.exitCode=1;
}
'''


class EmailNotifier:
    def __init__(self, brain: GBrain, recipient: str, checkout: str | Path | None = None):
        recipient = recipient.strip().lower()
        build_notification(recipient, "", subject="Memento", notification_id="validation")
        self.brain, self.recipient, self.ready = brain, recipient, False
        if checkout is None:
            entry = Path(brain.command[-1])
            if entry.name != "cli.ts":
                raise ValueError("Provide the pinned GBrain checkout for email delivery")
            checkout = entry.parent.parent
        self.checkout = Path(checkout).resolve()
        self.helper = brain.home / ".memento-email-notify.ts"
        self.ledger_path = brain.home / ".memento-email-sends.json"
        self.bridge = GBrain([brain.command[0], str(self.helper)], brain.home, timeout=brain.timeout)

    def _ledger(self) -> dict[str, Any]:
        try:
            return json.loads(self.ledger_path.read_text())
        except FileNotFoundError:
            return {}

    def _save(self, ledger: dict[str, Any]) -> None:
        temporary = self.ledger_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(ledger))
        temporary.chmod(0o600)
        temporary.replace(self.ledger_path)

    async def _request(self, mode: str, **payload) -> dict[str, Any]:
        """Caller holds the shared brain lock, including the send ledger."""
        self.helper.write_text(_BRIDGE)
        self.helper.chmod(0o600)
        return await self.bridge._execute(
            [str(self.checkout), mode],
            stdin=json.dumps({**payload, "recipient": self.recipient}),
        )

    async def connect(self) -> dict[str, Any]:
        self.ready = False
        async with self.brain._serialized():
            result = await self._request("connect")
        self.ready = bool(result.get("connected"))
        return result

    async def send(
        self, text: str, *, subject: str = "Memento", notification_id: str | None = None,
    ) -> str:
        notification_id = notification_id or str(uuid4())
        raw, message_id, intent = build_notification(
            self.recipient, text, subject=subject, notification_id=notification_id,
        )
        if not self.ready:
            await self.connect()
        async with self.brain._serialized():
            ledger = self._ledger()
            record = ledger.get(message_id)
            if record and record["intent"] != intent:
                raise GBrainError("email", "notification_id_reused_for_different_content")
            if record and record.get("message_id"):
                return record["message_id"]
            # Reconcile a crash after Gmail accepted but before local recording.
            found = await self._request("find", rfc_message_id=message_id)
            if found.get("message_id"):
                ledger[message_id] = {"intent": intent, "state": "sent", "message_id": found["message_id"]}
                self._save(ledger)
                return found["message_id"]
            if record and record.get("state") == "sending":
                # Gmail search can lag. Never blindly resend an uncertain POST.
                raise GBrainError("email", "send_outcome_unknown_wait_for_reconciliation")
            ledger[message_id] = {"intent": intent, "state": "sending"}
            self._save(ledger)
            try:
                result = await self._request("send", raw=raw)
            except GBrainError as error:
                rejected = re.fullmatch(r"gmail_send_http_(4\d\d)", error.code)
                # A definite API rejection (e.g. quota/auth) did not accept the
                # message. Runtime backoff may retry it. HTTP timeout, transport
                # errors, and 5xx outcomes remain uncertain and are reconciled.
                if rejected and rejected.group(1) != "408":
                    ledger[message_id] = {"intent": intent, "state": "rejected"}
                    self._save(ledger)
                raise
            ledger[message_id] = {"intent": intent, "state": "sent", "message_id": result["message_id"]}
            self._save(ledger)
            return result["message_id"]

    async def verify(self, message_id: str) -> dict[str, Any]:
        async with self.brain._serialized():
            return await self._request("verify", message_id=message_id)

    async def close(self) -> None:
        pass
