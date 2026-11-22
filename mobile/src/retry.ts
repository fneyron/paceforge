/** A page the server did not serve because it is being deployed: the proxy answers in its place for some seconds
 * (Coolify stops the app's containers before it starts the new ones — docs/ops-runbook.md, « Déployer »). The
 * shell asks for the page again before it says « Écran indisponible ». */

/** What the proxy answers while the app is away. Any other error is the app's own answer, and final. */
const AWAY = [502, 503, 504];
/** How long a page is asked for again. scripts/deploy/watch.py fails a deployment that leaves the site away for
 * longer than what this covers (its --max-outage, in deploy.yml). */
export const RETRY_BUDGET_MS = 60000;

/** The wait before asking again for a page answered with `status`, `elapsedMs` after its first failure and after
 * `tries` earlier retries; null when it must be reported: not the proxy's answer, or the budget is spent. */
export function retryDelay(status: number, elapsedMs: number, tries: number): number | null {
  if (!AWAY.includes(status)) return null;
  const wait = Math.min(1000 * 1.5 ** tries, 3000);
  return elapsedMs + wait > RETRY_BUDGET_MS ? null : wait;
}
