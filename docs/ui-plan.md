# SpriteForge Studio: the React UI

The user runs one command in the repo and lands in a chat-first studio on localhost. They
describe the game to the Director, watch the agents work in order, chat with any agent about its
work, give agents reference material, and get a playable game link at the end.

Decided 2026-11-03. The deadline for the hackathon video is early afternoon ET on 2026-11-04,
so phases 0-4 and 3b are the cut for the video; the rest follows the submission.

## What the user sees

### One command opens the studio

`python -m spriteforge` (no subcommand) starts the server on `127.0.0.1:8765` and opens the
browser. Home lists the projects (saved runs: thumbnail, title, status, cost) and a large
"Describe your game..." box that starts a new one. A first-run check explains a missing
`META_API_KEY` or Chromium in plain words and offers **Try offline (mock)** instead of failing.
Opening a project restores every chat exactly where it was (runs are durable and resumable).

The studio is for the one person on this machine: it binds to `127.0.0.1` only and has no
accounts. Because any website the user visits could still send requests to localhost, every
POST checks the `Origin`/`Host` headers (a cross-site upload or approval is refused).

### Layout

```
+--------------+----------------------------------+-----------------+
| AGENTS       |  Art Director              * busy | REFERENCES      |
| v Director   |                                  | cave.png        |
| v Designer   |  [art sheet: hero, slime, bat]   | style.pdf       |
| * Level      |  "Here are the 6 sprites..."     |                 |
| ! Art Dir. 2 |  +- G4 Playtest ---------------+ | COST            |
| o Pixel      |  | > Play v2  Approve | Changes | | 9 images        |
| o Menus      |  +-----------------------------+ | $0.21           |
| o Sound      |  you: make the bat purple        |                 |
| o Programmer |  queued for the next build       |                 |
| o QA         | +------------------------------+ |                 |
| o Reviewer   | | Message Art Director...  + > | |                 |
| o Librarian  | +------------------------------+ |                 |
+--------------+----------------------------------+-----------------+
```

**Left: the agents in execution order** (as a real run calls them): Director, Game Designer,
Level/Tile Designer, Art Director, Pixel Artist, Menu Designer, Sound Director, Programmer,
QA Playtester, Art Reviewer, Librarian. Each shows its status (waiting, working live, needs
you with a badge, done), what it produced and what it cost.

**Middle: the chat** with the selected agent. The Director's chat is the orchestrator: the
idea, the G1 brief questionnaire, and a timeline of everything.

**Right: references and cost** for the project and the selected agent.

### Gates are cards in the chat

Each gate (G1 brief, G2 design, G3 menus, G4 playtest, G5 final) appears in the Director's
chat and in the chat of the agent that owns it, with **Approve** and **Request changes**. The
play link appears from the first playable build (G4, Programmer and QA); the Librarian gives
the final link and `export.zip` at G5.

### Chatting with an agent

Agent chats feed the existing gated pipeline; they are not free-running agents. Nothing
irreversible happens without approval and accepted art stays locked. A message is either:

* **A question** ("why is the jump so short?"): answered now, one Spark call with that agent's
  own artifacts as context.
* **A change** ("make the bat purple"): filed under that agent's slot of the existing
  `Revision` model, so the Director never has to re-route it.

| Agent | Shows | A change goes to |
| --- | --- | --- |
| Director | idea, brief (G1), timeline | routed as today |
| Game Designer | research, plan, design, story | `design_changes`, `story_changes` |
| Level/Tile Designer | level previews | `level_changes` |
| Art Director, Pixel Artist | art sheet, animations, per-asset history | `art_changes` (only named entities are redrawn) |
| Menu Designer | every screen (G3) | `menu_changes` |
| Sound Director | sounds (playable), the sound editor | `sound_changes` |
| Programmer | build report, play link | `code_instructions` |
| QA Playtester | its checks and their results, **Suggest a check** | new `add_qa_checks`: the QA Playtester writes the check as a JS scenario (the L2.8 machinery vets and runs it) |
| Art Reviewer | scores, issues, Apply buttons | its existing suggestions |
| Librarian | final review (G5), lessons to promote, final play link | lesson promotion |

Where a change lands depends on where the agent is in the flow:

* **An agent that has not run yet:** the change is attached to it and used when it runs. Nothing
  stops.
* **The agent running now, or one that already finished:** **interrupt and rewind** (below).
* **The run is waiting at a gate:** the change is part of that gate's feedback.

### Interrupt and rewind

When the user changes the work of an agent that already finished (or is running), the agent
working now stops and the flow goes back to that agent's step, so no tokens are spent on work
the change would throw away.

1. The chat shows a card: "This stops the Programmer (build v2, $0.04 so far) and goes back to
   the Game Designer." **Go back** (default, Enter) or **Queue for later** (apply at the next
   gate instead).
2. **Go back** sets a stop request on the run. `Budget.reserve`, the one place every Muse call
   passes through, refuses to start any new call once it is set and raises `Cancelled`. Calls
   already in flight finish (they are paid for): their results are kept and land in the image
   cache, so an identical request later costs nothing.
3. The worker catches `Cancelled`, records the stopped step as superseded (kept for the record,
   never deleted), and turns the change into a `Revision` with that agent's slot filled.
4. The existing revision flow redoes only what the change is about, from that step forward:
   unaffected art stays locked and reused, unaffected code is edited rather than rewritten. The
   run continues to the next gate; earlier gates are not reopened.

`Cancelled` must not be swallowed by the broad handlers that keep the best repair attempt
(commit 97ce59b). It is raised from `reserve` and re-raised explicitly wherever exceptions are
caught; tests cover a stop during art, during code generation and during a repair round.

### Cost policy: caps stop loops, not the user

Call counters and the cost ledger stay mandatory and visible (right panel, per agent, per run).
What changes is what a cap does:

* **Work the user asks for always starts.** A gate response, Go back or Continue opens a fresh
  allowance for the autonomous work it triggers. Run totals never refuse a user's request, and
  an answer to a question is never refused or stopped.
* **Each agent has its own allowance: $10** (`budget.max_agent_cost`, estimated USD) since the
  user's last action (decided 2026-11-04; the per-kind call caps apply only to the legacy
  stages). The right panel shows each agent's spend against it.
* **Agent loops go on while they improve** (decided 2026-11-04): Programmer repairs, Muse
  redraws, QA check fixes, Menu and Level retries and Art Reviewer rounds (review, fix, review
  again) try again until either
  * the agent has used up its $10 allowance, or
  * no further improvement is possible: `game.patience` (2) tries in a row did not beat the
    best so far, the same failure came back twice in a row, or a try changed nothing.
  The fixed counts (`game.max_repairs`, `art_retry`, `max_art_calls`, `review_max_fixes`) are
  optional limits on top, unset by default. When a loop stops, the run does not end: it opens
  the gate it was heading to with the best result so far and a note ("the Programmer stopped
  after 3 repairs: the same jump test failed each time"). The user decides what next.
* `budget_exhausted` is no longer a final status. Old runs that ended that way can be resumed.
* **Recommended, to confirm:** one run-level spending ceiling in USD as a guard against a bug
  (hackathon credits are $150). Reaching it pauses with a question ("This project has spent
  $5. Raise the limit to $10 and continue?") instead of ending the run.

## Reference material

The paperclip in any chat, or a drop on the right panel. A reference belongs to one agent, or to
all agents when added in the Director's chat. Each upload is stored as a locked artifact, asks
the user to confirm they have the right to use it, and has a size cap.

* **Images:** to Spark (vision), and to Muse Image as a style reference for the art agents.
* **PDF, video:** Meta's model docs list image, video and PDF input for Muse Spark; the request
  shape is confirmed with one live call (config uses `muse-spark-1.3`, the docs list 1.1/1.2)
  before it is built. For the art agents, ffmpeg pulls keyframes from a video as image
  references when it is installed.

## Architecture

* `ui/`: Vite, React 18, TypeScript. TanStack Query for REST, one `EventSource` hook for the
  live event stream, plain CSS variables (no UI kit), the bundled pixel font for headings.
* The build output is committed to `src/spriteforge/app/static/`, so users never need Node.
  `npm run dev` proxies `/api` to FastAPI for development.
* The current UI stays at `/classic`: a fallback for the video, and the existing Playwright UI
  tests keep working.

### Backend (FastAPI)

| Endpoint | What it does |
| --- | --- |
| `GET /api/projects`, `POST /api/projects` | list runs; start a run from the first message. Today runs start only from the CLI and the server drives one fixed run: it gets one worker per active run (the largest backend change). |
| `GET /api/runs/{id}/agents` | the agent list: order, status, counts, cost (from events and gates) |
| `GET /api/runs/{id}/agents/{agent}/thread` | that agent's events, artifacts, gates and chat as one timeline |
| `POST /api/runs/{id}/agents/{agent}/messages` | `{text, attachments}`: a small classifier says question or change; a change is filed, queued or triggers a rewind |
| `POST /api/runs/{id}/stop` | the stop request behind **Go back** |
| `POST /api/runs/{id}/references` | upload (multipart), assigned to an agent |
| existing | gates, artifacts, events (SSE), play, sounds |

Chat messages are `chat.user` / `chat.agent` events in the existing append-only journal; no new
table.

## Build order

Each phase ends with pytest, a commit and a Playwright smoke test of the studio in mock mode.

| # | Phase | Estimate | In the video |
| --- | --- | --- | --- |
| 0 | `ui/` scaffold, FastAPI serves the build, bare `spriteforge` opens it, `/classic`, Origin check | 1 h | yes |
| 1 | Backend: projects, multiple runs, agents, threads, messages | 3-4 h | yes |
| 2 | React: sidebar, chat, gate cards, artifact cards (images, art sheet, play link, Markdown) | 4-5 h | yes |
| 3 | Agent changes into `Revision` slots; QA **Suggest a check** | 2 h | yes |
| 3b | Interrupt and rewind; cost policy (allowances, loop stops, no final `budget_exhausted`) | 3 h | yes |
| 4 | Image references | 1-2 h | if time allows |
| 5 | PDF and video references, after one live call | 2 h | after the deadline |
| 6 | Specialised cards (motion GIFs, sound editor, menus) and polish for the camera | open | partly |

Phases 0-4 and 3b are about 14-17 hours, which is more than the time left before recording.
Phase 6 is cut first: a card that is not ready links to that view in `/classic`. If time still
runs short, phase 4 goes next, then the "Queue for later" choice in 3b (Go back only).
