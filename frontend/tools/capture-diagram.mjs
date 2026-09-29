// Render the mermaid architecture diagram from docs/architecture.md to PNG.
// Reads the real diagram out of the doc so the image cannot drift from the source.
import { chromium } from '@playwright/test'
import { readFileSync } from 'node:fs'

import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// Repo root, so the scripts do not hardcode a checkout location.
const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

const doc = readFileSync(join(ROOT, 'docs/architecture.md'), 'utf8')
const blocks = [...doc.matchAll(/```mermaid\n([\s\S]*?)```/g)].map((m) => m[1])
if (blocks.length !== 1) throw new Error(`expected 1 mermaid block, found ${blocks.length}`)
console.log(`  diagram: ${blocks[0].split('\n').length} lines`)

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1500, height: 1100 }, deviceScaleFactor: 2 })

await page.setContent(
  `<!doctype html><html><body style="margin:0;background:#ffffff">
     <pre id="src" style="display:none">${blocks[0].replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' })[c])}</pre>
     <div id="out"></div>
   </body></html>`,
)
await page.addScriptTag({ url: 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js' })
await page.waitForFunction(() => typeof window.mermaid !== 'undefined', null, { timeout: 30_000 })

const ok = await page.evaluate(async () => {
  const src = document.getElementById('src').textContent
  try {
    const { svg } = await window.mermaid.render('diagram', src)
    document.getElementById('out').innerHTML = svg
    return true
  } catch (e) {
    return String(e)
  }
})
if (ok !== true) throw new Error(`mermaid failed: ${ok}`)

await page.waitForTimeout(700)
await page.locator('#out svg').screenshot({
  path: join(ROOT, '.screenshots/11-architecture.png'),
})
console.log('  shot 11-architecture')

await browser.close()
console.log('done')
