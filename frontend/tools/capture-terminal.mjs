// Render real recorded command output as a terminal-styled PNG.
// The text comes from files produced by actually running the commands.
import { chromium } from '@playwright/test'
import { readFileSync } from 'node:fs'

import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// Repo root, so the scripts do not hardcode a checkout location.
const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

const seed = readFileSync(join(ROOT, '.screenshots/term-seed.txt'), 'utf8')
const evalRun = readFileSync(join(ROOT, '.screenshots/term-eval.txt'), 'utf8')
const text = `${seed}\n${evalRun}`

// A secret must never reach an image. Fail loudly rather than ship one.
const FORBIDDEN = [
  /gsk_[A-Za-z0-9]{10,}/,
  /github_pat_[A-Za-z0-9_]{10,}/,
  /ghp_[A-Za-z0-9]{20,}/,
  /Authorization:/i,
  /HINDSIGHT_API_KEY\s*=\s*\S/,
  /GROQ_API_KEY\s*=\s*\S/,
  /BEGIN [A-Z ]*PRIVATE KEY/,
]
for (const pattern of FORBIDDEN) {
  if (pattern.test(text)) throw new Error(`refusing to render: matched ${pattern}`)
}
console.log('  secret scan: clean')

const escape = (s) => s.replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' })[c])

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1180, height: 800 }, deviceScaleFactor: 2 })
await page.setContent(
  `<!doctype html><html><body style="margin:0;background:#0b1120;padding:26px">
     <pre id="t" style="margin:0;font:13.5px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#e2e8f0;white-space:pre">${escape(text)}</pre>
   </body></html>`,
)
await page.locator('#t').screenshot({
  path: join(ROOT, '.screenshots/12-terminal.png'),
})
console.log('  shot 12-terminal')
await browser.close()
console.log('done')
