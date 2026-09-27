# Memento server API (contract between runtime and UI)

Server: FastAPI on http://127.0.0.1:8765. The UI is one static file served at `/`
(`src/memento/static/index.html`, no build step).

## HTTP

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/state` | - | `State` |
| GET | `/api/stream` | - | Server-Sent Events, one JSON `Event` per `data:` line |
| POST | `/api/chat` | `{"message": str}` | `{"reply": str, "surfaced": [Surfaced]}` |
| POST | `/api/clock` | `{"advance_minutes": int}` or `{"reset": true}` | `{"now": iso, "offset_minutes": int}` |
| POST | `/api/sync` | - | `{"ok": true}` (starts a Gmail/Calendar sync now) |
| POST | `/api/simulate/email` | `{"from": str, "subject": str, "body": str}` | `{"ok": true}` (injects an email event without Gmail) |
| POST | `/api/gmail/send` | `{"demo": "flight" \| "change" \| "nopa"}` | `{"ok": true, "id": str, "subject": str}` (sends a REAL Gmail message to the connected account; it arrives via GBrain sync ~10-15 s later) |

## Types

```ts
type State = {
  now: string;                 // ISO, includes demo clock offset
  offset_minutes: number;
  memories: MemoryView[];      // newest first
  feed: Event[];               // last ~200 events, oldest first
};

type MemoryView = {
  slug: string;                // unique key: page slug, prefixed "<source>:" unless it lives in the `mem` source
  path: string;                // the page's slug inside GBrain, e.g. "projects/acme-pilot"
  source: string;              // GBrain source id: "mem" or "default"
  origin: "memento" | "you";   // Memento wrote it from an email, or a person/agent wrote the page
  error: string | null;        // why the recipe doesn't run, if it doesn't
  title: string;
  body: string;                // markdown, what to remember
  recipe: string;              // the Python module (show it syntax-highlighted, monospace)
  sources: string[];           // slugs of the emails/events it came from
  expires: string | null;      // ISO
  status: "active" | "expired" | "error";
  triggers: TriggerView[];
  updated_at: string;          // ISO
};

type TriggerView = {
  id: string;                  // "<slug>#<n>"
  kind: "time" | "event";
  fire_at?: string;            // time triggers
  fired?: boolean;             // time triggers
  source?: "email" | "calendar" | "chat";   // event triggers
  question?: string;           // event triggers: the Jev question
  threshold?: number;
  actions: string[];           // e.g. ["alert: Your flight changed", "rewrite"]
};

type Surfaced = { slug: string; title: string; p: number };

type Event =
  | { type: "event_in"; id: string; source: "email" | "calendar" | "chat"; title: string; at: string }
  | { type: "jev"; event_id: string; source: string; ms: number; n_questions: number; input_tokens: number;
      results: { trigger_id: string; memory: string; question: string; p: number; threshold: number; fired: boolean }[] }  // top results, sorted by p desc
  | { type: "gate"; event_id: string; kind: string; p: number; decision: "write" | "skip" }
  | { type: "writer"; status: "start" | "done" | "error"; memory?: string; title?: string; ms?: number; attempts?: number; error?: string }
  | { type: "memory"; memory: MemoryView; change: "added" | "updated" }
  | { type: "fired"; trigger_id: string; memory: string; title: string; action: "alert" | "surface" | "rewrite"; text?: string; p?: number; at: string }
  | { type: "chat"; role: "user" | "assistant"; text: string; surfaced?: Surfaced[] }
  | { type: "clock"; now: string; offset_minutes: number }
  | { type: "sync"; status: "start" | "done" | "error"; new_events?: number; error?: string };
```

## UI layout (one page, dark, big-screen friendly)

- Header: product name "memento", the demo clock (`now`), buttons `+1h`, `+6h`, `+1 day`, `reset`, and `sync now`.
- Left: chat with the assistant. Surfaced memories appear as small chips under the assistant reply ("📌 Flight UA 123 · 0.96").
- Center: live feed. Each incoming email/event/chat is a card; its Jev call shows ms, number of questions,
  and bars for the top probabilities (fired ones highlighted). Alerts ("fired") are big and obvious.
  Writer activity shows "writing memory…" then the new memory title.
- Right: memories. Each card: title, body, status, and the recipe (Python, monospace, syntax-highlighted)
  with its triggers listed (time triggers show countdown relative to `now`; fired ones struck through).
  New/updated memories flash.
