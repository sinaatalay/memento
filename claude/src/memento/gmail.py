"""Send an email from sinajunks@gmail.com to itself, e.g. to play a demo email live.

    uv run python -m memento.gmail flight|change|nopa
    uv run python -m memento.gmail "Subject" "Body" ["Display name"]

The first run opens Google consent for the gmail.send scope; the token is cached in
../.context/gmail_send_token.json (gitignored). GBrain's own token stays read-only.
"""

import base64
import sys
from email.message import EmailMessage
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

ADDRESS = "sinajunks@gmail.com"
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
CONTEXT = Path(__file__).resolve().parents[3] / ".context"  # honiara/.context
TOKEN = CONTEXT / "gmail_send_token.json"

DEMO = {
    "flight": ("United Airlines", "Your trip confirmation: SFO to JFK, Mon Sep 28",
               "Thanks for choosing United. Confirmation number: K7Q2LM\n"
               "Flight UA 123, San Francisco (SFO) to New York (JFK)\n"
               "Monday, September 28, 2026. Departs 8:05 AM, arrives 4:41 PM. Seat 14C.\n"
               "Check-in opens 24 hours before departure."),
    "change": ("United Airlines", "Schedule change: UA 123 on Mon, Sep 28",
               "Your flight UA 123 from San Francisco (SFO) to New York (JFK) on Monday, September 28 "
               "now departs at 10:40 AM instead of 8:05 AM and arrives at 7:16 PM. "
               "Your seat 14C is unchanged. Confirmation K7Q2LM."),
    "soc2_planned": ("Vanta Audits", "SOC 2 audit: fieldwork scheduled for Oct 12",
                     "Your SOC 2 Type I audit is on track. Auditor fieldwork is scheduled for October 12-14, "
                     "and we expect to issue your report in early November. No action needed yet."),
    "soc2_issued": ("Vanta Audits", "Your SOC 2 Type I report is ready",
                    "Good news: your SOC 2 Type I report has been issued. You can download it and share it "
                    "with customers from your Trust Center today."),
    "nopa": ("Ana Lopez", "Dinner Friday at Nopa",
             "Confirming dinner with Ana at Nopa on Friday Oct 2 at 7:30pm, table for 4. "
             "She said she'd book it by Wednesday."),
}  # fmt: skip


def client_json() -> str:
    for line in (CONTEXT / ".env").read_text().splitlines():
        if line.startswith("GOOGLE_CLIENT_JSON="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("GOOGLE_CLIENT_JSON missing from .context/.env")


def credentials() -> Credentials:
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
        if creds.valid:
            return creds
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN.write_text(creds.to_json())
            return creds
    flow = InstalledAppFlow.from_client_secrets_file(client_json(), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, login_hint=ADDRESS)
    TOKEN.write_text(creds.to_json())
    TOKEN.chmod(0o600)
    return creds


def send(subject: str, body: str, name: str | None = None) -> str:
    message = EmailMessage()
    message["To"] = ADDRESS
    message["From"] = f"{name} <{ADDRESS}>" if name else ADDRESS
    message["Subject"] = subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    response = AuthorizedSession(credentials()).post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", json={"raw": raw}
    )
    response.raise_for_status()
    return response.json()["id"]


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 1 and args[0] in DEMO:
        name, subject, body = DEMO[args[0]]
    elif len(args) >= 2:
        subject, body, name = args[0], args[1], (args[2] if len(args) > 2 else None)
    else:
        raise SystemExit(__doc__)
    print("sent", send(subject, body, name), subject)
