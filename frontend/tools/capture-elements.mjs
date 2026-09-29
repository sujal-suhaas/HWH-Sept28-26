// Tight element shots from the incidents already in the store. No model calls:
// this only re-renders what the recorded runs produced.
import { chromium } from '@playwright/test'

import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// Repo root, so the scripts do not hardcode a checkout location.
const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

const OUT = join(ROOT, '.screenshots')
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } })

await page.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle' })
await page.waitForTimeout(2500)

const openIncident = async (id) => {
  await page.click(`button:has-text("${id}")`)
  await page.waitForTimeout(1800)
  console.log(`  opened ${id}: state=${await page.textContent('[data-testid="incident-state"]')}`)
}

const shoot = async (locator, name) => {
  const el = page.locator(locator)
  if (!(await el.count())) {
    console.log(`  MISSING ${name} (${locator})`)
    return
  }
  await el.first().scrollIntoViewIfNeeded()
  await page.waitForTimeout(400)
  await el.first().screenshot({ path: `${OUT}/${name}.png` })
  console.log(`  shot ${name}`)
}

// ---- memory ON: the resolved incident -------------------------------------
await openIncident('INC-9001')
await shoot('section[aria-label="Operator feedback"]', '07-on-feedback-resolved')
await shoot('[data-testid="proposal-card"]', '08-on-proposal')

const cards = await page.locator('[data-testid="proposal-card"]').count()
const states = await page.$$eval('[data-testid="proposal-state"]', (e) => e.map((x) => x.textContent))
console.log(`  proposal cards=${cards} states=${JSON.stringify(states)}`)

// the timeline, scoped to the panel that holds it
const timeline = await page.locator('section[aria-label*="Timeline"], [aria-label*="timeline"]').count()
console.log(`  timeline panels=${timeline}`)
await shoot('section:has-text("agent_run_finished")', '09-on-timeline')

// ---- memory OFF -----------------------------------------------------------
await openIncident('INC-9002')
await shoot('[data-testid="proposal-card"]', '10-off-proposal')

await browser.close()
console.log('done')
