#!/bin/sh
# Atomically publish a new Next.js static export into the nginx-served webout dir.
#
# Why not `rm -rf webout/* && tar xzf`:
#   1. It leaves the directory EMPTY for a moment — anything requested in that window
#      404s, and a Mini App loaded in that window shows "This page couldn't load".
#   2. Next.js chunk filenames are content-hashed. A client that loaded the OLD HTML
#      still asks for the OLD chunk names. Deleting them strands every open session
#      until the user gets fresh HTML — which Telegram's cached webview may not do for
#      a long time.
#
# So: overlay the new build on top (never empty the dir), swap the entry files in with
# atomic renames, and only then retire files that are both (a) absent from the new build
# and (b) old enough that no live session should still want them.
set -e

ROOT="${1:-$HOME/mk-projects/ludo}"
TARBALL="${2:-/tmp/ludo-webout.tgz}"
OUT="$ROOT/webout"
KEEP_DAYS="${KEEP_DAYS:-7}"

[ -f "$TARBALL" ] || { echo "no tarball at $TARBALL" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
tar xzf "$TARBALL" -C "$TMP"
[ -f "$TMP/index.html" ] || { echo "tarball has no index.html — refusing" >&2; exit 1; }

mkdir -p "$OUT"

# 1. New hashed assets first, so they exist BEFORE any HTML points at them.
if [ -d "$TMP/_next" ]; then
  cp -a "$TMP/_next/." "$OUT/_next/" 2>/dev/null || cp -a "$TMP/_next" "$OUT/"
fi

# 2. Everything else, entry files last, each via an atomic rename.
find "$TMP" -mindepth 1 -maxdepth 1 ! -name '_next' | while read -r src; do
  name="$(basename "$src")"
  if [ -d "$src" ]; then
    cp -a "$src" "$OUT/.stage-$name"
    rm -rf "$OUT/$name"
    mv "$OUT/.stage-$name" "$OUT/$name"
  else
    cp -a "$src" "$OUT/.stage-$name"
    mv -f "$OUT/.stage-$name" "$OUT/$name"
  fi
done

# 3. Retire only what's both absent from this build and older than the grace period.
find "$OUT" -type f -mtime "+$KEEP_DAYS" | while read -r f; do
  rel="${f#"$OUT"/}"
  [ -e "$TMP/$rel" ] || rm -f "$f"
done
find "$OUT" -type d -empty -delete 2>/dev/null || true

echo "published $(find "$TMP" -type f | wc -l) files; retired stale files older than ${KEEP_DAYS}d"
