"""Seed a demo run with realistic emails; the real writer turns each into a memory.

    uv run python scripts/seed.py --gmail    # send them as real Gmail messages (arrive via GBrain sync)
    uv run python scripts/seed.py            # inject them through /api/simulate/email instead
"""

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8765"

EMAILS = [
    ("Mission Dental <reminders@missiondental.com>", "Appointment reminder: Thu Oct 1, 3:30pm",
     "Hi Sina, this is a reminder of your cleaning with Dr. Patel on Thursday, October 1 at 3:30 PM at "
     "Mission Dental, 2100 Mission St. Please reschedule at least 24 hours in advance to avoid a $50 fee."),
    ("Priya Raman <priya@northwind.vc>", "Re: deck",
     "Thanks Sina! Looking forward to the investor deck. As discussed, can you send it by Wednesday Sep 30 "
     "end of day so I can share it with the partners on Thursday?"),
    ("Ana Lopez <ana.lopez@gmail.com>", "Birthday dinner Saturday!",
     "Hey! Confirming my birthday dinner this Saturday, October 3 at 7:30pm at Nopa. Table for 6. "
     "Don't forget you said you'd bring the cake :)"),
    ("Greystar Properties <noreply@greystar.com>", "October rent due Oct 1: autopay is off",
     "Your October rent of $2,450.00 for unit 4B is due on October 1. Autopay is currently turned off. "
     "Payments received after October 3 incur a $75 late fee."),
    ("Y Combinator <interviews@ycombinator.com>", "Your YC interview: Friday Oct 2, 10:00am PT",
     "Congratulations! Your interview for the Winter 2027 batch is scheduled for Friday, October 2 at 10:00 AM "
     "Pacific on Zoom. Please join 5 minutes early. Link: https://zoom.us/j/555-yc-w27"),
    ("The Standard, High Line <reservations@standardhotels.com>", "Reservation confirmed: Sep 28 - Sep 30",
     "Your reservation at The Standard, High Line, New York is confirmed: check-in Monday September 28 "
     "(after 3pm), check-out Wednesday September 30 (before 12pm). Confirmation 88213."),
    ("Apple <order_update@apple.com>", "Your MacBook Pro is out for delivery Tue Sep 29",
     "Your order W123456 will be delivered Tuesday, September 29 between 2 PM and 6 PM. A signature is "
     "required at delivery."),
    ("California DMV <noreply@dmv.ca.gov>", "Vehicle registration renewal due Oct 15",
     "The registration for your 2019 Honda Civic (plate 8ABC123) expires on October 15. Renew online at "
     "dmv.ca.gov; a smog check is required this year."),
    ("Substack <no-reply@substack.com>", "This week in AI: 12 links you missed",
     "Welcome to this week's roundup! 1. New models 2. Funding news 3. Cool demos ... Unsubscribe anytime."),
]  # fmt: skip


def post(path: str, payload: dict) -> None:
    request = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(), headers={"content-type": "application/json"}
    )
    urllib.request.urlopen(request, timeout=10).read()


if __name__ == "__main__":
    if "--gmail" in sys.argv:
        from memento import gmail

        for sender, subject, body in EMAILS:
            print("sent", gmail.send(subject, body, sender.split(" <")[0]), subject)
        post("/api/sync", {})
    else:
        for sender, subject, body in EMAILS:
            post("/api/simulate/email", {"from": sender, "subject": subject, "body": body})
            print("sent:", subject)
            time.sleep(3)
