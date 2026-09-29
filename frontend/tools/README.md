# Screenshot capture tools

The images in `docs/images/` are produced by these scripts, so they can be regenerated
rather than being hand-taken one-offs. Every image comes from a real run — no mock model,
no hand-written terminal output.

They need the app running (backend on `:8000`, vite on `:5173`) unless noted.

| Script | Produces | Needs the app | Costs model calls |
| --- | --- | --- | --- |
| `capture.mjs` | the memory ON/OFF flow: incident, Memory Inspector, operator confirmation | yes | yes |
| `capture-elements.mjs` | tight element shots from incidents already in the store | yes | no |
| `capture-diagram.mjs` | the architecture diagram, rendered from `docs/architecture.md` | no | no |
| `capture-terminal.mjs` | the seed + evaluation terminal image | no | no |
| `record-demo.mjs` | the browser half of the demo video, plus a cue sheet | yes | yes |

Run from `frontend/`:

```bash
node tools/capture-diagram.mjs
node tools/capture-terminal.mjs
node tools/record-demo.mjs
```

Output lands in `.screenshots/` (gitignored). Curated keepers are copied to `docs/images/`.

## Why these are committed

A screenshot in a README is a claim about what the system did. Committing the script that
produced it is what makes the claim checkable — and what makes it obvious if one was ever
faked, because the script would have to be faked too.

## Two rules these scripts enforce

- `capture.mjs` uses the real Hindsight bank and the real Groq models. It is not run against
  the E2E harness, whose model is a fake. An image of a fake model in a README is a claim, not
  a placeholder.
- `capture-terminal.mjs` refuses to render if the text matches an API key, a PAT, an
  `Authorization` header, or a private key, and throws instead of writing the image.

## Notes

- `capture-diagram.mjs` reads the mermaid block out of `docs/architecture.md`, so the image
  cannot drift from the diagram in the docs. It fails if the doc has zero or more than one
  mermaid block.
- `capture.mjs` selects a root cause before confirming, because `DIAGNOSIS_CONFIRMED` requires
  one and the `required` select otherwise blocks submission with no visible error.
- `capture-elements.mjs` reuses incidents already in the store, so re-running it is free.
- `record-demo.mjs` records one continuous browser session with `recordVideo` at 1920×1080
  and writes `.screenshots/demo.webm` plus `.screenshots/demo-cue-sheet.md`. It covers
  sections 1 and 3 of `content/video/script.md`; sections 2 and 4 are terminal screens that
  Playwright cannot record. Each beat is padded to at least its scripted length — padding can
  stretch a section but nothing can compress one, so a slow run overruns and the cue sheet
  reports the real number. The curated copy of the cue sheet lives at
  `content/video/cue-sheet.md`.
- `record-demo.mjs` opens a **new** incident for the replay rather than clicking "Re-run with
  memory". That button toggles the mode, so on a memory-ON incident it replays with memory
  OFF — the opposite of what the beat is demonstrating.
