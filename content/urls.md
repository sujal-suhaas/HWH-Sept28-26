# Publication tracker

The content phase is complete when every row has a real, public, working URL.

| Deliverable | Owner | URL |
| --- | --- | --- |
| Article | Member A | https://medium.com/@sujalsuhaas2007/our-hindsight-agent-cited-the-fix-but-never-named-the-cause-252c67697d06 |
| LinkedIn post | Member A | https://www.linkedin.com/feed/update/urn:li:activity:7510733117444231168/ |
| Reddit post | Member A | https://www.reddit.com/r/aiagents/comments/1wteqk1/a_missing_function_argument_made_our_incident/ |
| Article | Member B | |
| LinkedIn post | Member B | |
| Reddit post | Member B | |
| Team video (YouTube) | Team | |

## Verification status

Member A's article URL was supplied by Member A and recorded here. It has **not** been verified
independently: Medium returns 403 to plain HTTP fetches and blocks this development environment's IP
outright, so neither a request nor a real browser can load it from here. The checks below still need
a human to run them in an incognito window.

## Before filling a row

- [ ] The URL opens in an incognito window
- [ ] The three Hindsight links are present: [GitHub](https://github.com/vectorize-io/hindsight),
      [docs](https://docs.hindsight.vectorize.io/),
      [agent memory](https://vectorize.io/what-is-agent-memory)
- [ ] Screenshots render
- [ ] Code blocks render
- [ ] The title does not name the event
- [ ] For LinkedIn: under 800 characters, hook in the first two lines, 3–5 hashtags on the last line only
- [ ] For Reddit: the destination currently allows a link post of this kind
- [ ] For the video: the video is public and the thumbnail uses no invented numbers

## Drafts

| Draft | Path | Chars / length |
| --- | --- | --- |
| Member A article | [`member-a/article.md`](member-a/article.md) | ~1,600 words |
| Member A LinkedIn | [`member-a/linkedin.md`](member-a/linkedin.md) | 793 / 800 |
| Member A Reddit | [`member-a/reddit.md`](member-a/reddit.md) | link post, title + body |
| Member B article | [`member-b/article.md`](member-b/article.md) | ~1,350 words |
| Member B LinkedIn | [`member-b/linkedin.md`](member-b/linkedin.md) | 796 / 800 |
| Member B Reddit | [`member-b/reddit.md`](member-b/reddit.md) | link post, title + body |
| Video script | [`video/script.md`](video/script.md) | 3–4 min, 2 presenters |
| Thumbnail prompt | [`video/thumbnail_prompt.md`](video/thumbnail_prompt.md) | 16:9 |

Reddit posts are drafted as title + body per member. Each is a link post to the corresponding
article, so replace `{{ARTICLE_URL}}` with the article's published URL before submitting — and
confirm the destination currently allows a link post of this kind.

## Publishing order

1. Publish both articles somewhere public.
2. Fill in the article URLs.
3. Publish the LinkedIn posts, linking the article in the first comment.
4. Submit the Reddit link posts.
5. Upload the video.
6. Fill in every remaining row.
