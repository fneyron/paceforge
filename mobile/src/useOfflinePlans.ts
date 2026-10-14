import { useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import { trusted } from './api';
import { OfflineLibrary, OfflineRequest, offlineAccountScript, offlineFetchScript, offlineReply, readOfflinePlan, withPlan } from './offline';
import { loadOfflineLibrary, storeOfflineLibrary } from './offline-storage';
import { isLockedError } from './secure';

export function useOfflinePlans(inject: (script: string) => void, random: () => string) {
  const [library, setLibrary] = useState<OfflineLibrary>({ userId: null, plans: [] });
  const current = useRef(library);
  const queue = useRef(Promise.resolve());
  // A background launch can start on a locked phone: until the stored library has been
  // read, an account check or a write would replace it with an empty one.
  const libraryRead = useRef(false);
  const identity = useRef<number | null | undefined>(undefined);
  const account = useRef<OfflineRequest | null>(null);
  const pending = useRef<OfflineRequest | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [view, setView] = useState<'list' | number | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');

  function update(value: OfflineLibrary) { current.current = value; setLibrary(value); }
  function failed() { setNotice('La copie n’a pas pu être enregistrée. Vérifie la connexion et l’espace disponible, puis réessaie.'); }
  function finish() { pending.current = null; setBusy(false); if (timer.current) clearTimeout(timer.current); }
  function load() {
    queue.current = queue.current.then(async () => {
      if (libraryRead.current) return;
      try {
        update(await loadOfflineLibrary());
        libraryRead.current = true;
      } catch (e) {
        // Locked keychain, or not on screen: keep the stored copies and read them again on return.
        if (isLockedError(e) || AppState.currentState !== 'active') return;
        libraryRead.current = true;
        setNotice('Les copies locales ne sont pas lisibles. Enregistre à nouveau tes plans.');
      }
      if (libraryRead.current && identity.current !== undefined) await checkAccount(identity.current);
    }).catch(failed);
  }
  // Serialized with startup and writes; a late response cannot restore another account's copies.
  async function checkAccount(uid: number | null) {
    identity.current = undefined;
    if (current.current.userId === uid && uid !== null) return;
    setView(null); update({ userId: uid, plans: [] });
    await storeOfflineLibrary(current.current);
  }
  function change(get: (previous: OfflineLibrary) => OfflineLibrary) {
    const job = queue.current.then(async () => {
      if (!libraryRead.current) throw new Error('offline library not read yet');
      const next = get(current.current);
      if (next.userId !== current.current.userId) update({ userId: next.userId, plans: [] });
      await storeOfflineLibrary(next);
      update(next);
    });
    queue.current = job.catch(() => {});
    return job;
  }
  const reload = useRef(load);
  reload.current = load;
  useEffect(() => {
    reload.current();
    const subscription = AppState.addEventListener('change', state => { if (state === 'active' && !libraryRead.current) reload.current(); });
    return () => { subscription.remove(); if (timer.current) clearTimeout(timer.current); };
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
    const reply = offlineReply(account.current, raw, url);
    if (reply) {
      account.current = null;
      const uid = reply.user_id;
      if (uid !== null && !(typeof uid === 'number' && Number.isSafeInteger(uid) && uid > 0)) return true;
      // Not read yet: the latest identity is checked right after a successful read.
      identity.current = uid as number | null;
      queue.current = queue.current.then(async () => {
        if (libraryRead.current && identity.current !== undefined) await checkAccount(identity.current);
      }).catch(failed);
      if (!libraryRead.current) load();
      return true;
    }
    const task = pending.current, answer = offlineReply(task, raw, url);
    if (!answer || !task) return false;
    const plan = readOfflinePlan(answer.plan);
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
    finish(); account.current = null; identity.current = undefined; setView(null); setNotice('');
    // Also on an explicit logout request, even if the following page fails to load,
    // and even before the stored copies could be read: they must not survive a logout.
    const job = queue.current.then(async () => {
      update({ userId: null, plans: [] });
      await storeOfflineLibrary({ userId: null, plans: [] });
      libraryRead.current = true;
    });
    queue.current = job.catch(failed);
  }
  return { library, view, setView, busy, notice, loaded, save, receive, remove, forget };
}
