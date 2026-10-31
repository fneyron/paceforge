# Web assets

These pinned browser bundles are committed in `app/static/vendor`; production
needs neither npm nor a CDN for the application shell. To rebuild:

```sh
npm ci --ignore-scripts --prefix scripts/web-assets
npm run build --prefix scripts/web-assets
```

The lockfile records dependency integrity. HTMX, Alpine and Idiomorph are the
unchanged upstream minified distributions. Lucide is bundled with only the eight
icons currently used by `data-lucide` in the templates; add imports to `icons.mjs`
when adding another icon. `tests/browser/web-assets.cjs` checks that all template
icons are covered and that HTMX and Alpine initialize without external scripts.
Licenses accompany the served bundles. Alpine's license comes from the upstream
`v3.14.8/LICENSE.md` because its npm package omits that file; its bundled Vue
licenses are preserved as well.

All shared scripts are served with the deployed build ID in their URL. Only a
URL for the currently deployed build receives a long-lived immutable browser
cache policy. Unversioned and stale URLs revalidate, including the service
worker; the icons, images and manifest, which carry versions of their own
(`?v=5`), are kept a week. Cache policy is private because the session middleware can refresh a
session cookie even on static responses. HTML and account data are unaffected.

MapLibre and its map tiles remain on their providers: the trail map loads them
on demand when the map is opened, outside the common application shell.
