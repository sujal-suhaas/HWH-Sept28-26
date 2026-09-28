# CONTENT_GUIDE.md — Post-Launch Content Phase

**GATE:** Do not start this phase until `AGENTS.md` §14 is fully green and the project is running
successfully. This document covers the post-launch content workflow: two technical articles, two
LinkedIn posts, two Reddit link posts, and one team video.

The content must describe what is actually implemented and observed. Do not write around missing
features by assuming they exist.

---

## 0. Non-negotiable publishing rules

- The word **"hackathon"** must not appear in any article, LinkedIn post, Reddit title/body, video
description, thumbnail text, or hashtag.
- All content is in English.
- Articles must be published at public, linkable URLs using Medium, Dev.to, Hashnode, Substack, or
  LinkedIn Articles.
- Video is public on YouTube.
- Never use a private Google Drive link as the primary publication URL.
- Articles must naturally include these three links:
  - Hindsight GitHub: https://github.com/vectorize-io/hindsight
  - Hindsight docs: https://docs.hindsight.vectorize.io/
  - Vectorize agent memory: https://vectorize.io/what-is-agent-memory
- Do not fabricate benchmarks, latency, accuracy, adoption, or production scale.
- If the repository contains mocks, stubs, demo-only behavior, or limitations at publication time,
  describe them honestly.
- Future work must be explicitly labeled as future work.
- Never include secrets in screenshots, copied logs, code blocks, or article text.

---

## 1. Deliverables and ownership

| Deliverable | Owner | Repository path | Publishing destination |
| --- | --- | --- | --- |
| Article #1, 800–1,500 words | Member A | `content/member-a/article.md` | Medium / Dev.to / Hashnode / Substack / LinkedIn Articles |
| LinkedIn post #1 | Member A | `content/member-a/linkedin.md` | LinkedIn |
| Reddit link post #1 | Member A | URL tracked only | One approved subreddit |
| Article #2, 800–1,500 words | Member B | `content/member-b/article.md` | Same options |
| LinkedIn post #2 | Member B | `content/member-b/linkedin.md` | LinkedIn |
| Reddit link post #2 | Member B | URL tracked only | One approved subreddit |
| Team video, 2–5 minutes | Team | `content/video/script.md` | YouTube |
| Thumbnail prompt | Team | `content/video/thumbnail_prompt.md` | YouTube thumbnail generation |
| Published URL tracker | Team | `content/urls.md` | Repository |

Target **1,200–1,500 words** per article. This satisfies both the 800–1,500-word publication
requirement and the article prompt's broader target while keeping the article focused.

---

## 2. Evidence-first content workflow

Before running any writing prompt, inspect the repository's tracked, non-secret project files.

Safe inspection includes:

```bash
git ls-files
```

Read relevant source files, README, tests, demo script, evaluation output, and architecture docs.

Do **not** read or publish:

```text
.env
credential files
PATs/API keys
shell history containing credentials
private CI secrets
untracked credential dumps
```

Gitignore is not a security boundary. A file being ignored does not make it safe to expose to a
content-writing workflow.

### Evidence hierarchy

Use claims in this order of confidence:

1. actual code and tests
2. actual recorded demo output
3. actual evaluation output
4. documented design intent
5. explicitly labeled future work

Never turn item 4 or 5 into an item 1 claim.

---

## 3. Article workflow

### Step 1 — inspect the finished system

Before title generation, answer from the repository:

- What does DejaOps do?
- What part of Hindsight actually exists?
- What memories are retained?
- How are recall tags used?
- How is operator confirmation implemented?
- What does the Memory Inspector actually show?
- What did the learning evaluation actually measure?
- What was difficult or surprising?
- What limitation remains?

Write down any unsupported claim as a non-claim rather than filling the gap with assumption.

### Step 2 — title ideas

Run PROMPT 1 below.

Choose one title that accurately matches the implemented system.

### Step 3 — full article

Run PROMPT 2 with the chosen title.

Save to:

```text
content/<member>/article.md
```

### Step 4 — evidence pass

After generating the draft, manually verify:

- every technical claim against the repo
- every result against an actual recorded run
- every Hindsight claim against the current implementation
- every benchmark/result number against an evaluation artifact
- all three Hindsight/Vectorize links
- no secret or credential appears
- the article does not use the forbidden word

### Step 5 — screenshots

Capture:

1. the same alert with memory OFF vs memory ON
2. Memory Inspector showing a real recall/retain trace
3. terminal running the seed/evaluation command with credentials redacted
4. architecture diagram showing Hindsight Memory Bank placement
5. incident timeline with operator confirmation/correction

Do not show:

- API keys
- PATs
- `.env` contents
- Authorization headers
- private repository credentials

Crop tightly.

Use PNG for code/UI and JPEG for photos.

### Step 6 — publish

Publish publicly and verify:

- the URL works in an incognito/private session
- the three required Hindsight/Vectorize links are present
- screenshots load
- code blocks render
- article title does not mention the event/competition

---

## 4. Reddit workflow

Submit the article as a link post to one appropriate community, subject to current subreddit rules.
Possible communities include:

- `r/llmdevs`
- `r/sideproject`
- `r/aiagents`
- `r/aimemory`

Before posting, confirm that the chosen community currently permits this kind of link post.

The post should:

- be about the technical idea/result
- open with a concrete observation
- explain where Hindsight is used
- contain at least one real code snippet in the article
- contain a concrete before/after example
- include one honest limitation or lesson
- include screenshots
- use a public article URL
- avoid event/competition framing

---

## 5. LinkedIn workflow

Publish only after the corresponding article is public.

Requirements:

- under 800 characters
- project GitHub repository link in the main post
- first 2 lines contain the hook and no hashtags
- 3–7 concrete technical takeaways
- at least one before/after behavior change caused by memory
- positive mention of Hindsight agent memory
- no event/competition framing
- 3–5 relevant hashtags on the last line only

Suggested hashtags:

```text
#AIAgents #AI #Hindsight #AgentMemory #AIMemory #LLM
```

Do not use `#Hackathon` or student-related tags.

First comment: article URL.

Second comment:

```text
Here's Hindsight if you want to check it out: https://github.com/vectorize-io/hindsight
```

---

## 6. Video workflow

The total video target is **3–4 minutes**, which fits the required 2–5 minute range.

### Structure

1. 30 sec — intro
2. 30 sec — problem
3. 2 min — live demo
4. 30 sec — takeaway

Total target: approximately 3.5 minutes.

### Recording rules

- 1080p minimum
- record the screen directly
- terminal font large enough to read
- close notifications
- use a talking head if natural, but screen-only is acceptable
- show actual project behavior, not a mock replay of an imagined implementation

### Demo sequence

1. Fire the curated incident.
2. Run memory OFF.
3. Show the generic/uncertain result.
4. Run the same normalized incident with memory ON.
5. Show the recalled incident/memory evidence.
6. Show the Memory Inspector trace.
7. For the learning story, run the novel scenario before teaching.
8. Explicitly confirm/correct the diagnosis and resolution through the UI.
9. Replay the same scenario.
10. Show the measured before/after evaluation result.

Do not cut from "agent was wrong" directly to "agent learned" without showing the operator action
that created the authoritative memory.

---

## 7. Prompt 1 — Title Ideas

You are an experienced software engineer who writes high-performing technical articles for
skeptical developers on sites like Hacker News, Reddit, and personal blogs.

Your task: generate 20 possible article titles about the project in this repo.

Find a unique angle to highlight the actual implementation of Hindsight memory.

Do not mention the event/competition anywhere in the titles.

Each title must:

- mention Hindsight
- hint at a real story, failure, design decision, or measurable observation
- focus on one specific idea, decision, or problem
- make another engineer curious enough to investigate

Constraints:

- audience: experienced engineers
- voice: first person where natural
- style: short, concrete, specific
- no more than 10 words
- no hype or clickbait
- no claims that are not supported by the repository

Look through the actual tracked code and docs to identify the best candidate topic.

Output exactly 20 titles, one per line, no numbering, no commentary.

---

## 8. Prompt 2 — Full Article Draft

You are a senior software engineer writing a first-person technical article for experienced
engineers.

You are looking at a real repository. The article must describe the implementation that is actually
present in the repository at the time of writing.

Do not assume that placeholders, mocks, or future integrations have already become production
systems. Do not silently upgrade the implementation in prose.

Do not mention the event/competition anywhere in the article.

### Goal

Choose one clear technical story from the repository and explain:

- what I built
- why I built it that way
- how the memory architecture works
- where Hindsight helped
- what went wrong or was difficult
- what the actual test/demo showed
- what I would change next

### Voice

- first person: "I" / "we"
- concrete and mildly opinionated
- technical, not promotional
- no buzzwords
- no invented benchmark or scale claims
- no fake production claims

### Evidence rules

Use only:

1. code actually present
2. tests actually present
3. evaluation output actually produced
4. clearly labeled design intent
5. clearly labeled future work

When a limitation exists, say so directly.

### Structure

1. Hook
2. What the system does
3. Core technical story
4. 2–4 small real code snippets
5. Concrete observed behavior
6. What I learned
7. Limitations / next step

### Hindsight links

Naturally include:

- [Hindsight GitHub](https://github.com/vectorize-io/hindsight)
- [Hindsight documentation](https://docs.hindsight.vectorize.io/)
- [Vectorize agent memory](https://vectorize.io/what-is-agent-memory)

### Hindsight-specific accuracy

Use current terminology:

- Memory Bank = isolated Hindsight memory container
- `bank_id` identifies the bank
- tags are used for scoped recall/filtering
- metadata carries context/source information and is not itself the recall filter

### Output

Output only finished Markdown:

- one H1 title
- `##` sections
- real code blocks from the repository
- no fabricated code
- no fabricated metrics

---

## 9. Prompt 3 — LinkedIn Post

You are a technical LinkedIn writer for developers, AI engineers, and AI enthusiasts.

Write one post about the implemented project.

Do not mention the event/competition.

### Hook

The first two lines should create curiosity around a real engineering problem or misconception.
Do not put links or hashtags in those first two lines.

### Body

Use short paragraphs and 3–7 concrete technical takeaways.

At least one takeaway must show an observed before/after behavior caused by Hindsight memory.

Mention Hindsight agent memory positively, but explain what it actually did in this system.

### Accuracy

Do not say:

- "production" unless the repository genuinely supports that claim
- "X% better" unless the evaluation output contains X%
- "zero failures" unless tested and recorded

### Links and hashtags

Include the project's GitHub repository link in the main post.

Last line only:

```text
#AIAgents #AI #Hindsight #AgentMemory #AIMemory #LLM
```

Return only the final post text under 800 characters.

---

## 10. Prompt 4 — Video Script & Titles

Look at the actual finished repository and write a 3–4 minute screen-recorded demo script.

My name: `[YOUR NAME]`

The script must describe actual files, commands, endpoints, and UI states found in the repository.

### Structure

1. 30 sec — who I am and what DejaOps does
2. 30 sec — problem without memory
3. 2 min — live demo
4. 30 sec — takeaway

### Demo cues

For each section include:

- narration
- exact screen action
- exact file/command/endpoint where applicable

The live demo should show:

- memory OFF
- memory ON
- Memory Inspector
- operator confirmation/correction
- teach → retain → replay
- actual evaluation output where available

Do not invent terminal output or API responses.

Also provide five video title options.

---

## 11. Prompt 5 — Thumbnail

Generate a 16:9 YouTube thumbnail based on the actual video script.

The thumbnail should communicate the real product story:

```text
WITHOUT MEMORY → generic diagnosis
WITH MEMORY → prior incident + validated fix
```

Do not claim fake benchmark percentages or unsupported outcomes.

Keep text minimal and legible.

---

## 12. Content files

Recommended layout:

```text
content/
├── member-a/
│   ├── article.md
│   └── linkedin.md
├── member-b/
│   ├── article.md
│   └── linkedin.md
├── video/
│   ├── script.md
│   └── thumbnail_prompt.md
└── urls.md
```

Each member commits their content using their own identity through `scripts/git-as.sh`.

---

## 13. Final publication tracker — `content/urls.md`

Use this table:

| Deliverable | Owner | URL |
| --- | --- | --- |
| Article | Member A | |
| LinkedIn post | Member A | |
| Reddit post | Member A | |
| Article | Member B | |
| LinkedIn post | Member B | |
| Reddit post | Member B | |
| Team video (YouTube) | Team | |

The content phase is complete only when every row has a real public URL.

---

## 14. Final content QA checklist

### Accuracy

- [ ] Every factual technical claim can be traced to code, tests, or recorded output.
- [ ] No imagined production implementation is presented as existing.
- [ ] No invented benchmark is presented.
- [ ] Learning claims cite actual before/after evidence.
- [ ] Hindsight Memory Bank/tag/metadata terminology is accurate.

### Secret safety

- [ ] No API keys appear.
- [ ] No GitHub PAT appears.
- [ ] No Authorization header appears.
- [ ] No `.env` screenshot appears.
- [ ] Terminal screenshots were reviewed for secrets.

### Publishing

- [ ] The event/competition name is absent.
- [ ] Hindsight GitHub link is present.
- [ ] Hindsight docs link is present.
- [ ] Vectorize agent memory link is present.
- [ ] Article is public and linkable.
- [ ] LinkedIn post is under 800 characters.
- [ ] Reddit destination currently allows the intended link-post format.
- [ ] YouTube video is public.
- [ ] `content/urls.md` is fully populated.

### Quality

- [ ] The article has a real engineering story rather than a generic project summary.
- [ ] The before/after memory behavior is concrete.
- [ ] At least one limitation or lesson is honest and specific.
- [ ] Screenshots support the story.
- [ ] Human review was performed before publishing.

---

## 15. Final instruction

AI output is a draft, not the publication authority.

Edit the generated material until it sounds like the actual engineers who built DejaOps. Prefer a
specific true detail over a polished but unsupported claim.
