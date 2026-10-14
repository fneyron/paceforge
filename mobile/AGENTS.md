# PaceForge mobile

This companion shares the authenticated PaceForge web screens. Read `README.md`
before changing sync semantics or supported health categories.

- Keep one WebView mounted for the web session and native pairing bridge. Its
  native tabs change the web destination without creating competing cookies.
- Check the installed Expo major version and its versioned documentation before
  changing native APIs. Use `npx expo install` for SDK-managed dependencies.
- HealthKit / Health Connect require a native build; Expo Go is insufficient.
- Never edit generated `ios/` or `android/` sources. Use `app.json` and the local
  config plugin, then run `npx expo prebuild --no-install`.
- Never sum a direct watch total with a phone copy. Mobile daily data stays out
  of the recovery calculation. Keep units, dates and source provenance explicit.
- Health permissions are read-only and optional. Empty reads are not zeros.
- Background sync (README « Synchronisation en arrière-plan »): `src/background.ts`
  is imported by `index.ts` before the app and runs without any screen. Never
  import React, `App` or the WebView there. Subscribe to HealthKit changes only
  there (one native callback per type, the last one wins); register background
  types only from the foreground. Its logic lives in `background-engine.ts`,
  tested with fakes. Never log health values.
- Keychain access goes through `src/secure.ts` (readable after the first unlock).
  A locked keychain (`KeychainLocked`) is never « not paired » and must never
  replace stored data with an empty value.
- Before completing changes: `npm run lint`, `npm run typecheck`, `npm test`,
  `npx expo prebuild --no-install`, `python3 scripts/check-background-sync.py`
  on the generated projects, the native harness (`tests/browser/native/*.cjs`,
  see `docs/mobile-fluidite.md`), and export both iOS and Android bundles.
- Signed native distribution requires the owner's developer accounts. Do not
  claim an export of JavaScript bundles is an installed or tested native app.
