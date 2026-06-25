#!/usr/bin/env bash
# Rebuild the Tailwind CSS from the templates. Run after adding new utility
# classes (the committed app/static/css/tailwind.css is prebuilt — classes not
# present in it silently do nothing).
set -euo pipefail
cd "$(dirname "$0")/.."
printf '@tailwind base;\n@tailwind components;\n@tailwind utilities;\n' > /tmp/pf-tw-input.css
npx -y tailwindcss@3 -c tailwind.config.js -i /tmp/pf-tw-input.css -o app/static/css/tailwind.css --minify
echo "Rebuilt app/static/css/tailwind.css"
