# Demo recording — cue sheet

Recorded from `content/video/script.md`, sections 1 and 3 (browser only).
Sections 2 and 4 are terminal screens and are recorded separately; the editor
splices them in.

**Video:** `.screenshots/demo.webm` (1920×1080, 8:45)

| Start | End | Beat | Target | Actual |
| --- | --- | --- | --- | --- |
| 0:02 | 0:32 | Intro — workspace, memory toggle | 30s | 30s |
| 0:32 | 1:29 | 3a — memory OFF | 30s | 56.4s **overran** |
| 1:29 | 4:11 | 3b — memory ON | 40s | 162.0s **overran** |
| 4:11 | 4:46 | 3c — operator confirmation | 35s | 35s |
| 4:46 | 5:48 | 3d — teach (novel alert, no match) | 30s | 62.3s **overran** |
| 5:48 | 8:07 | 3d — replay (same alert, now matches) | 30s | 138.5s **overran** |

## What the overruns are

The four overrunning beats are not the model thinking. They are **HTTP 429 rate
limits** from the Groq free tier. The backend log for this recording contains 62
`429 Too Many Requests` responses against the primary model, each followed by a
retry backoff (the SDK's own retry waits 31s).

The screen is not frozen during these windows — the composer shows the run in
progress — but nothing new appears. They are the segments to speed up or cut.

## What the recording actually shows

The point of the demo is the pair at the end, and it reproduces:

| | teach run | replay run |
| --- | --- | --- |
| Alert | `webhook-dispatcher`, p1, `data_corruption` | same |
| Memory mode | ON | ON |
| Causes proposed | **none** (`causes=[]`) | **RC-009** |
| Incident state | waiting for operator | waiting for operator |

The teach run recalled no cause because nothing in the bank matched the alert.
The operator then confirmed `RC-009` as the diagnosis and `RB-051` as the
runbook, which is what writes the memory. The replay run — a fresh incident on
the identical alert — recalled that memory and named `RC-009` first.

Earlier in the same recording:

| | memory OFF | memory ON |
| --- | --- | --- |
| Alert | `checkout-api` p99 latency | same |
| Causes proposed | none shown | `RC-004` |
| Incident state | waiting for operator | waiting for operator |

Then the operator confirmed the diagnosis (`RC-004`) and the resolution
(`RB-032`); the incident reached `resolved` with **both** cards reading
`confirmed by operator`.

## Editing notes

- The four model waits are marked above. Speeding them up 8–16× turns 8:45 into
  roughly 1:30 of footage that matches the script's beat lengths.
- Beat 3c (0:35) is the only beat at target length and is the one worth playing
  at full speed — the two confirmations and the resulting `resolved` state.
- The terminal sections (2 and 4) can reuse the existing still at
  `docs/images/seed-and-evaluation.png` if re-recording them is inconvenient.
