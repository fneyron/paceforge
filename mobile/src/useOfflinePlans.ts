import { useEffect, useRef, useState } from 'react';
import { trusted } from './api';
import { OfflineLibrary, OfflineRequest, offlineAccountScript, offlineFetchScript, offlineReply, readOfflinePlan, withPlan } from './offline';
import { loadOfflineLibrary, storeOfflineLibrary } from './offline-storage';

export function useOfflinePlans(inject: (script: string) => void, random: () => string) {
  const [library, setLibrary] = useState<OfflineLibrary>({ userId: null, plans: [] });
  const current = useRef(library);
  const queue = useRef(Promise.resolve());
  const account = useRef<OfflineRequest | null>(null);
  const pending = useRef<OfflineRequest | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [view, setView] = useState<'list' | number | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');

  function update(value: OfflineLibrary) { current.current = value; setLibrary(value); }
  function failed() { setNotice('La copie n’a pas pu être enregistrée. Vérifie la connexion et l’espace disponible, puis réessaie.'); }
  function finish() { pending.current = null; setBusy(false); if (timer.current) clearTimeout(timer.current); }
  function change(get: (previous: OfflineLibrary) => OfflineLibrary) {
    const job = queue.current.then(async () => {
      const next = get(current.current);
      if (next.userId !== current.current.userId) update({ userId: next.userId, plans: [] });
      await storeOfflineLibrary(next);
      update(next);
    });
    queue.current = job.catch(() => {});
    return job;
  }
  useEffect(() => {
    queue.current = loadOfflineLibrary().then(update).catch(() => setNotice('Les copies locales ne sont pas lisibles. Enregistre à nouveau tes plans.'));
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, []);

  function loaded(url: string) {
    if (!trusted(url)) return;
    if (pending.current && pending.current.url !== url) finish();
    account.current = { nonce: random(), url };
    inject(offlineAccountScript(account.current.nonce));
  }
  function save(routeId: number, url: string) {
    if (pending.current || !trusted(url, `/simulator/routes/${routeId}`)) return;
    const task = { nonce: random(), url, routeId };
    pending.current = task; setBusy(true); setNotice('');
    timer.current = setTimeout(() => { finish(); failed(); }, 30000);
    inject(offlineFetchScript(task));
  }
  function receive(raw: string, url: string) {
    const identity = offlineReply(account.current, raw, url);
    if (identity) {
      account.current = null;
      const uid = identity.user_id;
      if (uid !== null && !(typeof uid === 'number' && Number.isSafeInteger(uid) && uid > 0)) return true;
      // Serialize with startup and writes; a late response cannot restore another account's copies.
      queue.current = queue.current.then(async () => {
        if (current.current.userId === uid && uid !== null) return;
        setView(null); update({ userId: uid, plans: [] });
        await storeOfflineLibrary(current.current);
      }).catch(failed);
      return true;
    }
    const task = pending.current, reply = offlineReply(task, raw, url);
    if (!reply || !task) return false;
    const plan = readOfflinePlan(reply.plan);
    pending.current = null; if (timer.current) clearTimeout(timer.current);
    if (!plan || plan.route_id !== task.routeId) { finish(); failed(); return true; }
    void change(previous => {
      if (previous.userId !== plan.user_id) throw new Error('account changed');
      return withPlan(previous, plan);
    }).then(() => { setView(plan.route_id); setNotice(''); }).catch(failed).finally(finish);
    return true;
  }
  function remove(routeId: number) {
    void change(previous => ({ ...previous, plans: previous.plans.filter(p => p.route_id !== routeId) }))
      .then(() => { setView('list'); setNotice(''); })
      .catch(() => setNotice('La copie n’a pas pu être supprimée. Réessaie.'));
  }
  function forget() {
    finish(); account.current = null; setView(null); setNotice('');
    // Also on an explicit logout request, even if the following page fails to load.
    void change(() => ({ userId: null, plans: [] })).catch(failed);
  }
  return { library, view, setView, busy, notice, loaded, save, receive, remove, forget };
}
