import { build } from 'esbuild';
import { copyFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const vendor = path.resolve(here, '../../app/static/vendor');
await mkdir(path.join(vendor, 'licenses'), { recursive: true });
for (const [source, target] of [
  ['node_modules/htmx.org/dist/htmx.min.js', 'htmx-2.0.4.min.js'],
  ['node_modules/alpinejs/dist/cdn.min.js', 'alpine-3.14.8.min.js'],
  ['node_modules/idiomorph/dist/idiomorph.min.js', 'idiomorph-0.3.0.min.js'],
  ['node_modules/htmx.org/LICENSE', 'licenses/htmx-2.0.4.txt'],
  ['licenses/alpinejs-3.14.8.txt', 'licenses/alpine-3.14.8.txt'],
  ['node_modules/@vue/reactivity/LICENSE', 'licenses/vue-reactivity.txt'],
  ['node_modules/@vue/shared/LICENSE', 'licenses/vue-shared.txt'],
  ['node_modules/idiomorph/LICENSE', 'licenses/idiomorph-0.3.0.txt'],
  ['node_modules/lucide/LICENSE', 'licenses/lucide-0.469.0.txt'],
]) await copyFile(path.join(here, source), path.join(vendor, target));
await build({
  entryPoints: [path.join(here, 'icons.mjs')],
  outfile: path.join(vendor, 'lucide-0.469.0.min.js'),
  bundle: true, minify: true, format: 'iife', target: 'safari16.4',
  legalComments: 'inline',
});
