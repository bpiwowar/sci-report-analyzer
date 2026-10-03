#!/bin/sh
# Extracts the UI strings and merges them into the catalogs (then translate the new
# entries of src/sci_report_analyzer/locales/<lang>/LC_MESSAGES/messages.po).
set -e
cd "$(dirname "$0")/.."
pkg=src/sci_report_analyzer
uv run pybabel extract --no-location --sort-output -k _ -k N_ -k ngettext:1,2 \
    --project "SciReport Analyzer" -o "$pkg/locales/messages.pot" "$pkg"
for po in "$pkg"/locales/*/LC_MESSAGES/messages.po; do
    uv run pybabel update --no-fuzzy-matching --ignore-obsolete -i "$pkg/locales/messages.pot" \
        -o "$po" -l "$(basename "$(dirname "$(dirname "$po")")")"
done
