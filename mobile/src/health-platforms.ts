// Compile-time only. TypeScript checks importers against the health.ts fallback,
// while Metro bundles health.ios.ts or health.android.ts: both must offer its API.
// The linter resolves './health' with platform extensions, hence the disabled rule.
/* eslint-disable import/no-duplicates */
import type * as fallback from './health';
import type * as ios from './health.ios';
import type * as android from './health.android';
/* eslint-enable import/no-duplicates */

type Api = typeof fallback;
type Offers<T extends Api> = T;
export type HealthPlatforms = [Offers<Pick<typeof ios, keyof Api>>, Offers<Pick<typeof android, keyof Api>>];
