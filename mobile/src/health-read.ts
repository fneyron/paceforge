export class HealthReadTimeout extends Error {
  constructor() { super('Lecture santé trop longue. Réessaie.'); }
}

export function readIssue(label: string, error: unknown): string {
  if (error instanceof HealthReadTimeout) return `${label} : lecture interrompue par le délai maximal. Réessaie en gardant l’application ouverte.`;
  return `${label} : lecture indisponible. Réessaie avec le téléphone déverrouillé.`;
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
