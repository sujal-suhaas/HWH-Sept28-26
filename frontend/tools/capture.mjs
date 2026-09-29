// Live capture for the content phase. Real Hindsight bank, real Groq models.
// Output goes to .screenshots/ (gitignored); keepers get curated into docs/images/.
import { chromium } from '@playwright/test'

import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// Repo root, so the scripts do not hardcode a checkout location.
const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

const OUT = join(ROOT, '.screenshots')
const URL = 'http://127.0.0.1:5173'

// Runbook -> root cause, read from data/seed/runbooks.json so the script does not
// hardcode a mapping that the seed can change.
const { readFileSync } = await import('node:fs')
const CAUSE_OF = Object.fromEntries(
  JSON.parse(readFileSync(join(ROOT, 'data/seed/runbooks.json'), 'utf8')).runbooks.map(
    (r) => [r.id, r.root_cause_id],
  ),
)

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } })

const shot = async (name, opts = {}) => {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true, ...opts })
  console.log(`  shot ${name}`)
}

const waitForRun = async (label) => {
  console.log(`  waiting for ${label} ...`)
  // NB: the signature is (fn, arg, options) - passing options second silently
  // makes it the `arg` and the 30s default applies instead.
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
  await page.waitForTimeout(3000)
}

const read = (sel) => page.textContent(sel).catch(() => null)

await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)

const options = await page.$$eval('select[aria-label="Alert"] option', (els) =>
  els.map((e) => e.textContent),
)
const idx = options.findIndex((o) => o.includes('checkout'))
console.log(`  alert: ${options[idx]}`)

// ---- 1. memory ON: a real grounded run ------------------------------------
await page.selectOption('select[aria-label="Alert"]', { index: idx })
await page.click('[data-testid="memory-mode-on"]')
await page.click('button:has-text("Open incident with memory")')
await waitForRun('memory ON')

console.log(`  ON state=${await read('[data-testid="incident-state"]')}`)
const states = await page.$$eval('[data-testid="proposal-state"]', (e) => e.map((x) => x.textContent))
console.log(`  proposals: ${JSON.stringify(states)}`)
const causes = await page.$$eval('[data-testid="proposal-root-cause"]', (e) =>
  e.map((x) => x.textContent),
)
console.log(`  root causes shown: ${JSON.stringify(causes)}`)
await shot('01-memory-on')

// ---- 2. Memory Inspector: a real recall/retain trace ----------------------
const inspector = page.locator('[aria-label="Memory inspector"]')
await inspector.scrollIntoViewIfNeeded()
await page.waitForTimeout(800)
await inspector.screenshot({ path: `${OUT}/02-memory-inspector-on.png` })
console.log('  shot 02-memory-inspector-on')
const pills = await page.$$eval('[data-testid="memory-state"]', (e) => e.map((x) => x.textContent))
console.log(`  memory states: ${JSON.stringify(pills)}`)

// ---- 3. operator confirmation -> timeline --------------------------------
// DIAGNOSIS_CONFIRMED needs a root cause and the select is `required`, so the
// browser blocks submit with no POST when the agent named no cause. Pick one
// explicitly; prefer the runbook's cause so the pairing stays coherent.
const pickRootCause = async () => {
  const sel = page.locator('select[aria-label="Confirmed root cause"]')
  if (!(await sel.count())) return null
  if (await sel.inputValue()) return await sel.inputValue()
  const rb = await page.$eval('select[aria-label="Runbook"]', (el) => el.value).catch(() => '')
  const cause = rb ? CAUSE_OF[rb] : null
  await sel.selectOption(cause ? { value: cause } : { index: 1 })
  return await sel.inputValue()
}

await page.selectOption('select[aria-label="Outcome"]', { value: 'DIAGNOSIS_CONFIRMED' })
const chosen = await pickRootCause()
console.log(`  confirmed root cause=${chosen}`)
await page.click('form button[type="submit"]')
await page.waitForTimeout(4000)
console.log(`  after diagnosis: outcome=${await read('[data-testid="operator-outcome"]')}`)

await page.selectOption('select[aria-label="Outcome"]', { value: 'RESOLUTION_CONFIRMED' })
await page.waitForTimeout(500)
const rbSel = page.locator('select[aria-label="Runbook"]')
if (!(await rbSel.inputValue())) {
  // Match the runbook to the cause the operator just confirmed, so the recorded
  // pair is coherent. Falling back to index 1 paired RC-004 with RB-005.
  const match = Object.entries(CAUSE_OF).find(([, cause]) => cause === chosen)
  await rbSel.selectOption(match ? { value: match[0] } : { index: 1 })
}
const rb = await rbSel.inputValue()
const fix = await page.$eval('textarea[aria-label="Validated fix"]', (el) => el.value).catch(() => '')
if (!fix) {
  await page.fill(
    'textarea[aria-label="Validated fix"]',
    'Scaled the payments-ledger consumer group and replayed the affected partition.',
  )
}
console.log(`  runbook=${rb}`)
await page.click('form button[type="submit"]')
await page.waitForTimeout(4500)

console.log(`  after confirm: state=${await read('[data-testid="incident-state"]')}`)
console.log(`  outcome=${await read('[data-testid="operator-outcome"]')}`)
await shot('03-confirmed-timeline')

const inspector2 = page.locator('[aria-label="Memory inspector"]')
await inspector2.scrollIntoViewIfNeeded()
await page.waitForTimeout(800)
await inspector2.screenshot({ path: `${OUT}/04-memory-inspector-retain.png` })
console.log('  shot 04-memory-inspector-retain')
const pills2 = await page.$$eval('[data-testid="memory-state"]', (e) => e.map((x) => x.textContent))
console.log(`  memory states after retain: ${JSON.stringify(pills2)}`)

// ---- 4. same alert, memory OFF -------------------------------------------
await page.click('[data-testid="memory-mode-off"]')
await page.click('button:has-text("Open incident with memory")')
await waitForRun('memory OFF')
console.log(`  OFF state=${await read('[data-testid="incident-state"]')}`)
await shot('05-memory-off')

const inspector3 = page.locator('[aria-label="Memory inspector"]')
await inspector3.scrollIntoViewIfNeeded()
await page.waitForTimeout(800)
await inspector3.screenshot({ path: `${OUT}/06-memory-inspector-off.png` })
console.log('  shot 06-memory-inspector-off')

await browser.close()
console.log('done')
