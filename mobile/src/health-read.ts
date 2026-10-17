export class HealthReadTimeout extends Error {
  constructor() { super('Lecture santé trop longue. Réessaie.'); }
}

export class HealthAuthorizationRequired extends Error {}
export class HealthDataLocked extends Error {}

// Nitro can expose an NSError as properties or just its description. Only a
// specific HealthKit refusal identifies a locked store; arbitrary JS/native
// failures (including processing errors) do not establish that the phone locks.
function healthKitCode(error: unknown): number | undefined {
  if (!error || typeof error !== 'object') return;
  const e = error as { domain?: unknown; code?: unknown; message?: unknown };
  if (e.domain === 'HKErrorDomain' && Number.isInteger(Number(e.code))) return Number(e.code);
  if (typeof e.message === 'string') {
    const match = e.message.match(/\bHKErrorDomain\b\s+Code=(\d+)\b/);
    if (match) return Number(match[1]);
  }
}

export function readIssue(label: string, error: unknown): string {
  if (error instanceof HealthReadTimeout) return `${label} : lecture interrompue par le délai maximal. Réessaie en gardant l’application ouverte.`;
  const code = healthKitCode(error);
  const message = error instanceof Error ? error.message : '';
  if (error instanceof HealthAuthorizationRequired || code === 5 || /\bauthorization (?:is )?not determined\b/i.test(message)) {
    return `${label} : autorisation à demander. Touche « Synchroniser » pour l’accorder.`;
  }
  if (error instanceof HealthDataLocked || code === 6 || /\bprotected health data is inaccessible\b/i.test(message)) {
    return `${label} : données protégées indisponibles. Déverrouille l’iPhone, puis réessaie en gardant PaceForge ouvert.`;
  }
  return `${label} : lecture indisponible. Réessaie dans Réglages → Apple Santé. Si l’erreur persiste, vérifie que PaceForge est à jour.`;
}

// Native health reads cannot be cancelled through these SDKs. Bound the wait;
// late results have no side effects and cannot resume an abandoned sync phase.
export function healthReader(budgetMs: number, perReadMs = 8000) {
  const until = Date.now() + budgetMs;
  return async function read<T>(query: () => Promise<T>): Promise<T> {
    const remaining = until - Date.now();
    if (remaining <= 0) throw new HealthReadTimeout();
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([query(), new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new HealthReadTimeout()), Math.min(remaining, perReadMs));
      })]);
    } finally { if (timer) clearTimeout(timer); }
  };
}
