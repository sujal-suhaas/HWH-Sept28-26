// Records the browser-driven part of content/video/script.md as one video.
//
// Sections 2 and 4 of the script are terminal screens; Playwright only records
// the browser, so those are recorded separately and spliced by the editor. This
// covers section 1 (intro) and section 3 (the live demo).
//
// Each beat is padded to at least its scripted duration. Padding can stretch a
// section but nothing can compress one, so a slow model run overruns its window
// and the cue sheet reports the real number rather than the intended one.
import { chromium } from '@playwright/test'
import { readFileSync, writeFileSync, mkdirSync, renameSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const OUT = join(ROOT, '.screenshots')
const URL = 'http://127.0.0.1:5173'
const CAUSE_OF = Object.fromEntries(
  JSON.parse(readFileSync(join(ROOT, 'data/seed/runbooks.json'), 'utf8')).runbooks.map((r) => [
    r.id,
    r.root_cause_id,
  ]),
)

mkdirSync(OUT, { recursive: true })

// Scripted durations, from content/video/script.md section 1 and section 3.
const BEATS = [
  { id: 'intro', target: 30, label: 'Intro - UI, empty incident list' },
  { id: 'memory_off', target: 30, label: '3a - memory OFF' },
  { id: 'memory_on', target: 40, label: '3b - memory ON' },
  { id: 'confirm', target: 35, label: '3c - operator confirmation' },
  { id: 'teach', target: 30, label: '3d - teach (novel alert, no match)' },
  { id: 'replay', target: 30, label: '3d - replay (same alert, now matches)' },
]

const browser = await chromium.launch()
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  recordVideo: { dir: OUT, size: { width: 1920, height: 1080 } },
})
const page = await context.newPage()

const t0 = Date.now()
const marks = []
const waits = []
let current = null

const start = (id) => {
  const beat = BEATS.find((b) => b.id === id)
  current = { id, label: beat ? beat.label : id, at: Date.now() - t0 }
  console.log(`  [${fmt(current.at)}] ${id}`)
}
const end = async (targetSeconds) => {
  const elapsed = (Date.now() - t0) / 1000 - current.at / 1000
  const hold = targetSeconds - elapsed
  if (hold > 0) await page.waitForTimeout(hold * 1000)
  const rec = {
    ...current,
    target: targetSeconds,
    actual: +((Date.now() - t0) / 1000 - current.at / 1000).toFixed(1),
  }
  marks.push(rec)
  console.log(
    `      target ${targetSeconds}s  actual ${rec.actual}s` +
      (rec.actual > targetSeconds ? '  <-- overran' : ''),
  )
  current = null
}
const fmt = (ms) => {
  const s = ms / 1000
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}

const runButton = () =>
  [...document.querySelectorAll('button')].find((b) =>
    b.textContent?.includes('Open incident with memory'),
  )

const waitForRun = async () => {
  const from = (Date.now() - t0) / 1000
  await page.waitForFunction(
    () => {
      const b = [...document.querySelectorAll('button')].find((x) =>
        x.textContent?.includes('Open incident with memory'),
      )
      return b && !b.disabled
    },
    null,
    { timeout: 420_000 },
  )
  const to = (Date.now() - t0) / 1000
  if (current) waits.push({ beat: current.id, from, to })
  await page.waitForTimeout(2500)
}

const openIncident = async (index, mode) => {
  await page.selectOption('select[aria-label="Alert"]', { index })
  await page.click(`[data-testid="memory-mode-${mode}"]`)
  await page.click('button:has-text("Open incident with memory")')
  await waitForRun()
}

const inspector = () => page.locator('[aria-label="Memory inspector"]')

await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForTimeout(2000)

// ---- 1. Intro -------------------------------------------------------------
// No incident is selected yet, so the Inspector and the proposal cards are not
// mounted. This beat is the empty workspace and the memory toggle only.
start('intro')
await page.hover('[data-testid="memory-mode-on"]')
await page.waitForTimeout(5000)
await page.hover('[data-testid="memory-mode-off"]')
await page.waitForTimeout(4000)
await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }))
await page.waitForTimeout(4000)
await end(30)

// ---- 3a. memory OFF -------------------------------------------------------
start('memory_off')
const opts = await page.$$eval('select[aria-label="Alert"] option', (e) =>
  e.map((x) => x.textContent),
)
const checkout = opts.findIndex((o) => o.includes('checkout'))
console.log(`      alert[${checkout}]: ${opts[checkout]}`)
await openIncident(checkout, 'off')
console.log(`      state=${await page.textContent('[data-testid="incident-state"]')}`)
await page.waitForTimeout(3000)
await inspector().scrollIntoViewIfNeeded()
await page.waitForTimeout(6000)
await end(30)

// ---- 3b. memory ON --------------------------------------------------------
start('memory_on')
await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }))
await page.waitForTimeout(1500)
await page.click('button:has-text("Re-run with memory")')
await waitForRun()
console.log(`      state=${await page.textContent('[data-testid="incident-state"]')}`)
console.log(
  `      causes=${JSON.stringify(await page.$$eval('[data-testid="proposal-root-cause"]', (e) => e.map((x) => x.textContent)))}`,
)
await page.waitForTimeout(4000)
await inspector().scrollIntoViewIfNeeded()
await page.waitForTimeout(7000)
await end(40)

// ---- 3c. operator confirmation -------------------------------------------
start('confirm')
await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }))
await page.waitForTimeout(1500)

const feedback = page.locator('section[aria-label="Operator feedback"]')
await feedback.scrollIntoViewIfNeeded()
await page.waitForTimeout(2000)

await page.selectOption('select[aria-label="Outcome"]', { value: 'DIAGNOSIS_CONFIRMED' })
await page.waitForTimeout(2500)
const rcSel = page.locator('select[aria-label="Confirmed root cause"]')
const chosen = (await rcSel.count()) ? await rcSel.inputValue() : null
console.log(`      confirmed root cause=${chosen}`)
await page.click('form button[type="submit"]')
await page.waitForTimeout(4000)
console.log(`      outcome=${(await page.textContent('[data-testid="operator-outcome"]')).slice(0, 70)}`)

const readState = async () => {
  await page.waitForTimeout(1200)
  return page.textContent('[data-testid="incident-state"]')
}

await page.selectOption('select[aria-label="Outcome"]', { value: 'RESOLUTION_CONFIRMED' })
await page.waitForTimeout(2000)
const rbSel = page.locator('select[aria-label="Runbook"]')
if (!(await rbSel.inputValue())) {
  const match = Object.entries(CAUSE_OF).find(([, c]) => c === chosen)
  await rbSel.selectOption(match ? { value: match[0] } : { index: 1 })
}
const fix = await page.$eval('textarea[aria-label="Validated fix"]', (el) => el.value).catch(() => '')
if (!fix) {
  await page.fill(
    'textarea[aria-label="Validated fix"]',
    'Scaled the payments-ledger consumer group and replayed the affected partition.',
  )
}
await page.waitForTimeout(2000)
await page.click('form button[type="submit"]')
await page.waitForTimeout(5000)
console.log(`      state=${await readState()}`)

// Show the diagnosis card survived the later resolution confirmation.
const states = await page.$$eval('[data-testid="proposal-state"]', (e) => e.map((x) => x.textContent))
console.log(`      proposal states=${JSON.stringify(states)}`)
await page.waitForTimeout(3000)
await inspector().scrollIntoViewIfNeeded()
await page.waitForTimeout(6000)
await end(35)

// ---- 3d. teach, then replay ----------------------------------------------
start('teach')
await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }))
await page.waitForTimeout(1500)
await openIncident(0, 'on')
console.log(`      novel alert state=${await page.textContent('[data-testid="incident-state"]')}`)
console.log(
  `      causes=${JSON.stringify(await page.$$eval('[data-testid="proposal-root-cause"]', (e) => e.map((x) => x.textContent)))}`,
)
await page.waitForTimeout(3000)
await inspector().scrollIntoViewIfNeeded()
await page.waitForTimeout(6000)
await end(30)

start('replay')
await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }))
await page.waitForTimeout(1500)
// The operator supplies the real cause and fix. The teach has to write BOTH a
// confirmed DIAGNOSIS carrying the cause and a confirmed RESOLUTION carrying the
// runbook, because that is the pair scripts/evaluate_learning.py teaches and the
// pair the replay's two recalls look for. An OPERATOR_CORRECTION alone writes a
// correction with no cause id and no runbook, so the replay recalls the incident
// but has nothing to name.
await feedback.scrollIntoViewIfNeeded()
await page.selectOption('select[aria-label="Outcome"]', { value: 'DIAGNOSIS_CONFIRMED' })
await page.waitForTimeout(2000)
await page.selectOption('select[aria-label="Confirmed root cause"]', { value: 'RC-009' })
await page.waitForTimeout(2000)
await page.click('form button[type="submit"]')
await page.waitForTimeout(4000)
console.log(`      taught diagnosis: state=${await readState()}`)

await page.selectOption('select[aria-label="Outcome"]', { value: 'RESOLUTION_CONFIRMED' })
await page.waitForTimeout(2000)
await page.selectOption('select[aria-label="Runbook"]', { value: 'RB-051' })
await page.fill(
  'textarea[aria-label="Validated fix"]',
  'Re-enabled the previous signing key as a secondary, verified the backlog drained, then retired it.',
)
await page.waitForTimeout(2000)
await page.click('form button[type="submit"]')
await page.waitForTimeout(4000)
console.log(`      taught resolution: state=${await readState()}`)

// Hindsight indexes asynchronously, so a replay fired immediately can miss the
// memory the correction just wrote. Hold here and narrate over it.
await page.waitForTimeout(12_000)

// Open a NEW incident on the same alert with memory ON. The "Re-run with
// memory" button toggles the mode, so on an ON incident it would replay with
// memory OFF - which is the opposite of the point of this beat.
await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }))
await page.waitForTimeout(1500)
await openIncident(0, 'on')
console.log(`      replay state=${await readState()}`)
console.log(
  `      replay causes=${JSON.stringify(await page.$$eval('[data-testid="proposal-root-cause"]', (e) => e.map((x) => x.textContent)))}`,
)
await page.waitForTimeout(4000)
await inspector().scrollIntoViewIfNeeded()
await page.waitForTimeout(6000)
await end(30)

// ---- save ----------------------------------------------------------------
const video = page.video()
await context.close()
const raw = await video.path()
await browser.close()
const videoPath = join(OUT, 'demo.webm')
renameSync(raw, videoPath)

const total = (Date.now() - t0) / 1000
const sheet = [
  '# Demo recording — cue sheet',
  '',
  `Recorded from content/video/script.md, sections 1 and 3 (browser only).`,
  `Sections 2 and 4 are terminal screens and are recorded separately.`,
  '',
  `**Video:** \`${videoPath.replace(/\\/g, '/')}\``,
  `**Length:** ${fmt(total * 1000)} (${total.toFixed(1)}s)`,
  '',
  '| Start | End | Beat | Target | Actual |',
  '| --- | --- | --- | --- | --- |',
  ...marks.map((m) => {
    const endAt = m.at / 1000 + m.actual
    const flag = m.actual > m.target ? ' **overran**' : ''
    return `| ${fmt(m.at)} | ${fmt(endAt * 1000)} | ${m.label}${flag} | ${m.target}s | ${m.actual}s |`
  }),
  '',
  'Overruns are the model thinking, not dead air — narrate over them.',
  '',
  '## Model waits',
  '',
  'Each window below is time the agent spent on a live model call. The screen',
  'shows the run in progress, but nothing new appears. These are the segments to',
  'speed up or trim; the beats around them are the footage worth keeping at speed.',
  '',
  '| From | To | Length | Beat |',
  '| --- | --- | --- | --- |',
  ...waits.map(
    (w) =>
      `| ${fmt(w.from * 1000)} | ${fmt(w.to * 1000)} | ${(w.to - w.from).toFixed(1)}s | ${w.beat} |`,
  ),
  '',
  `Total model wait: ${waits.reduce((a, w) => a + (w.to - w.from), 0).toFixed(1)}s of ${total.toFixed(1)}s.`,
  '',
].join('\n')

const sheetPath = join(OUT, 'demo-cue-sheet.md')
writeFileSync(sheetPath, sheet)
console.log(`\n  video: ${videoPath}`)
console.log(`  sheet: ${sheetPath}`)
console.log(`  total: ${fmt(total * 1000)}`)
