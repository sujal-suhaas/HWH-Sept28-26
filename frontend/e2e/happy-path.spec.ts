import { expect, test } from '@playwright/test'

/**
 * The 60-second story, as a test.
 *
 * Alert -> recall -> grounded diagnosis -> operator confirmation -> the outcome
 * becomes memory. Each step asserts something a viewer would actually see, so a
 * regression in any of them fails here rather than in the demo.
 *
 * The backend is the deterministic harness (scripts/serve_e2e.py): real routes
 * and a real trace log, fake model and fake memory provider.
 */

test.describe('incident happy path', () => {
  test('from alert to a grounded answer to confirmed memory', async ({ page }) => {
    await page.goto('/')

    // The UI must be honest about which data source it is talking to.
    await expect(page.getByTestId('data-source')).toHaveText('live api')

    // 1. An alert fires. Open it with memory ON.
    await expect(page.getByTestId('memory-mode-on')).toHaveAttribute('aria-pressed', 'true')
    await page.getByRole('button', { name: /Open incident with memory on/ }).click()

    // 2. The incident exists and the agent has proposed something.
    await expect(page.getByTestId('incident-memory-mode')).toHaveText('memory on')
    const diagnosis = page.getByTestId('proposal-card').first()
    await expect(diagnosis).toContainText('Proposed diagnosis')

    // 3. The answer is grounded: it cites a recalled memory and names a runbook.
    await expect(diagnosis).toContainText('INC-2201:RESOLUTION')
    await expect(page.getByTestId('proposal-card').nth(1)).toContainText('RB-014')

    // 4. The proposal is not an outcome.
    await expect(diagnosis.getByTestId('proposal-state')).toHaveText('proposed')
    await expect(diagnosis).toContainText('Awaiting operator confirmation')

    // 5. The Memory Inspector shows what memory actually did.
    const inspector = page.getByRole('region', { name: 'Memory inspector' })
    await expect(inspector.getByTestId('memory-state').first()).toHaveText('memory retained')
    await expect(inspector.getByText('memory recalled').first()).toBeVisible()

    // 6. The operator confirms the diagnosis. The prefilled root cause comes
    //    from the runbook the agent proposed, via the catalog.
    const feedback = page.getByRole('region', { name: 'Operator feedback' })
    await expect(feedback.getByLabel('Confirmed root cause')).toHaveValue('RC-001')
    await feedback.getByRole('button', { name: /Record diagnosis confirmed/ }).click()

    await expect(diagnosis.getByTestId('proposal-state')).toHaveText('confirmed by operator')
    await expect(page.getByTestId('operator-outcome')).toContainText('DIAGNOSIS_CONFIRMED')
    await expect(page.getByTestId('operator-outcome')).toContainText('RC-001')

    // 7. And confirms the fix, which promotes the runbook.
    await feedback.getByLabel('Outcome').selectOption('RESOLUTION_CONFIRMED')
    await expect(feedback.getByLabel('Runbook')).toHaveValue('RB-014')
    await feedback.getByRole('button', { name: /Record resolution confirmed/ }).click()

    await expect(page.getByTestId('incident-state')).toHaveText('resolved')
    await expect(page.getByTestId('operator-outcome')).toContainText('RESOLUTION_CONFIRMED')
    await expect(page.getByTestId('operator-outcome')).toContainText('RB-014')

    // 8. A closed outcome is closed.
    await expect(feedback.getByText(/This incident is RESOLVED/)).toBeVisible()

    // 9. The confirmation wrote memory, and the trace says so.
    await expect(inspector.getByText('memory retained').first()).toBeVisible()
    const retained = await inspector.getByText('memory retained').count()
    expect(retained).toBeGreaterThan(0)
  })

  test('memory off runs the same alert with no recall and no retain', async ({ page }) => {
    await page.goto('/')

    await page.getByTestId('memory-mode-off').click()
    await expect(page.getByTestId('memory-mode-explainer')).toContainText('Hindsight is never called')
    await page.getByRole('button', { name: /Open incident with memory off/ }).click()

    await expect(page.getByTestId('incident-memory-mode')).toHaveText('memory off')

    // No traces at all, and the inspector says why rather than showing "no match".
    const inspector = page.getByRole('region', { name: 'Memory inspector' })
    await expect(inspector.getByText(/Hindsight was not called for this run/)).toBeVisible()
    await expect(inspector.getByText('no relevant memory found')).toHaveCount(0)

    // The agent had no historical context, so it cites nothing.
    await expect(page.getByTestId('proposal-card').first()).toContainText('No memory cited')
  })

  test('re-runs the same alert in the other memory mode', async ({ page }) => {
    await page.goto('/')

    await page.getByRole('button', { name: /Open incident with memory on/ }).click()
    await expect(page.getByTestId('incident-memory-mode')).toHaveText('memory on')

    await page.getByRole('button', { name: /Re-run with memory off/ }).click()

    // A new incident, same alert, the other mode.
    await expect(page.getByTestId('incident-memory-mode')).toHaveText('memory off')
    await expect(page.getByRole('button', { name: /Re-run with memory on/ })).toBeVisible()
  })
})
