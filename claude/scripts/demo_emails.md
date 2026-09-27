# Demo emails

The console's "Send real Gmail" buttons send these from sinajunks@gmail.com to itself
(`POST /api/gmail/send {"demo": key}`; texts in `src/memento/gmail.py`). They come back
through GBrain's Gmail sync in ~10-15 s.

| Key | Subject | Expected |
|---|---|---|
| `soc2_planned` | SOC 2 audit: fieldwork scheduled for Oct 12 | Acme page stays quiet (~0.01) |
| `soc2_issued` | Your SOC 2 Type I report is ready | Acme fires (~0.98): alert + update appended to the page |
| `flight` | Your trip confirmation: SFO to JFK, Mon Sep 28 | new memory + recipe written by River |
| `change` | Schedule change: UA 123 on Mon, Sep 28 | only the flight fires: alert + rewrite to 10:40 |
| `nopa` | Dinner Friday at Nopa | new memory |

From a terminal: `uv run python -m memento.gmail soc2_issued`.
