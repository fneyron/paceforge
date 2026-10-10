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
- Before completing changes: `npm run lint`, `npm run typecheck`, `npm test`,
  `npx expo prebuild --no-install`, and export both iOS and Android bundles.
- Signed native distribution requires the owner's developer accounts. Do not
  claim an export of JavaScript bundles is an installed or tested native app.
