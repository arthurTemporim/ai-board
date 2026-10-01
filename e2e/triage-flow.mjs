// Browser smoke test of the AI triage flow, in MOCK_AI mode against a freshly seeded database.
//   make e2e            (starts an isolated stack, runs this, stops it)
// Flow: submit a card that duplicates a seeded one -> run triage -> see the merge suggestion ->
// accept it -> votes consolidate on the primary card -> metrics page shows the decision.
import { chromium } from 'playwright';

const BASE = process.env.E2E_URL ?? 'http://localhost:5273';
let failures = 0;
const check = (name, ok, extra = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? '  ' + extra : ''}`);
  if (!ok) failures++;
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
const api = (path, init) => page.evaluate(([p, i]) => fetch('/api' + p, i).then((r) => r.json()), [path, init]);

try {
  await page.goto(BASE);
  await page.getByRole('region', { name: 'Inbox' }).waitFor();

  // The seeded original: card 1, 7 votes, none of them from the acting user (Alice Martin).
  const original = await api('/cards/1');
  check('seeded original is the SAML card with 7 votes', original.title === 'Support SAML single sign-on' && original.vote_count === 7);

  // 1. Submit a new card that says the same thing in slightly different words.
  await page.getByRole('button', { name: 'New card' }).click();
  await page.getByLabel('Title').fill('Support SAML single sign-on please');
  await page.getByLabel('Description').fill(original.description);
  await page.getByRole('button', { name: 'Create card' }).click();
  const drawer = page.getByRole('dialog', { name: /Card: Support SAML single sign-on please/ });
  await drawer.waitFor();
  const created = (await api('/cards?q=please')).find((c) => c.title.endsWith('please'));
  check('new card was created', Boolean(created), `id ${created?.id}`);

  // 2. The requester votes for their own card.
  await drawer.getByRole('button', { name: /^Vote/ }).click();
  await drawer.getByText('1 vote', { exact: true }).waitFor();

  // 3. Run AI triage and watch the progress finish.
  await drawer.getByRole('button', { name: 'Run AI triage' }).click();
  const progress = drawer.getByRole('status', { name: 'AI triage progress' });
  await progress.getByText(/Finished: \d+ of \d+ steps/).waitFor({ timeout: 60_000 });
  check('progress shows the run finished', true, (await progress.innerText()).split('\n')[0]);
  check('run reports a duplicate outcome', /looks like a duplicate/.test(await progress.innerText()));

  // 4. The merge suggestion, with confidence and reasoning.
  const suggestion = drawer.getByRole('listitem', { name: 'Possible duplicate of #1 Support SAML single sign-on' });
  await suggestion.waitFor();
  const text = await suggestion.innerText();
  check('duplicate suggestion shows confidence', /\d+% sure/.test(text));
  check('duplicate suggestion shows the reasoning', /Why: /.test(text));
  check('duplicate suggestion says votes will move', /moves 1 vote\(s\) from #\d+ to #1/.test(text));
  check('the card tile is flagged for review', await page.evaluate((id) => document.querySelector(`[data-card-id="${id}"]`)?.getAttribute('aria-label')?.includes('AI suggestion'), created.id));
  await page.screenshot({ path: 'out/suggestions.png' });

  // 5. Nothing was applied yet.
  check('before accepting, the original still has 7 votes', (await api('/cards/1')).vote_count === 7);

  // 6. Accept the merge.
  await suggestion.getByRole('button', { name: /^Merge duplicate:/ }).click();
  await page.getByText(/Merged: 1 vote\(s\) moved to card 1/).waitFor();
  const after = await api('/cards/1');
  const dup = await api(`/cards/${created.id}`);
  check('votes consolidated on the primary card', after.vote_count === 8, `${original.vote_count} -> ${after.vote_count}`);
  check('the duplicate card has no votes left', dup.vote_count === 0);
  const link = dup.links.find((l) => l.card_id === 1);
  check('the original stays linked as a confirmed duplicate', link?.kind === 'duplicate' && link?.status === 'confirmed');
  await suggestion.waitFor({ state: 'detached' });
  check('the suggestion is gone from the panel', true);

  // 7. Audit trail.
  await drawer.getByRole('button', { name: 'Show audit trail' }).click();
  const trail = drawer.getByRole('list', { name: 'Audit trail' });
  await trail.getByText(/duplicate merged/).waitFor();
  check('audit trail records the merge and the acceptance', /suggestion accepted/.test(await trail.innerText()));

  // 8. The "Needs review" filter: other suggestions are still waiting on this card.
  await page.keyboard.press('Escape');
  await drawer.waitFor({ state: 'detached' });
  const toolbar = page.getByRole('search', { name: 'Filter cards' });
  await toolbar.getByLabel('Needs review').check();
  const visible = await page.locator('[data-card-id]').count();
  check('"Needs review" narrows the board to cards with something waiting', visible >= 1 && visible < 5, `${visible} card(s)`);
  await page.screenshot({ path: 'out/needs-review.png' });
  await toolbar.getByLabel('Needs review').uncheck();

  // 9. Metrics page.
  await page.getByRole('navigation', { name: 'Views' }).getByRole('button', { name: 'Metrics' }).click();
  await page.getByRole('heading', { name: 'Triage metrics' }).waitFor();
  const merged = page.getByRole('region', { name: 'Duplicates consolidated' });
  check('metrics: one duplicate consolidated', (await merged.innerText()).includes('1'));
  check('metrics: acceptance rate is computed', /\d+(\.\d)?%/.test(await page.getByRole('region', { name: 'Suggestion acceptance' }).innerText()));
  check('metrics: baseline is labelled as an assumption', await page.getByText(/assumption, not a measurement/).first().isVisible());
  await page.screenshot({ path: 'out/metrics.png' });

  check('no browser errors', errors.length === 0, errors.join(' | '));
} catch (error) {
  console.log('FAIL  unexpected error:', error.message);
  await page.screenshot({ path: 'out/failure.png' }).catch(() => {});
  failures++;
}
await browser.close();
console.log(failures === 0 ? 'ALL PASSED' : `${failures} FAILED`);
process.exit(failures ? 1 : 0);
