#!/usr/bin/env bash
# Tests the frame decoder on this PC against real dashboard screens.
# Needs gcc, python3 with Pillow, and the uzlib sources that ship with the ESP8266 core:
#   UZLIB_SRC=~/.platformio/packages/framework-arduinoespressif8266/tools/sdk/uzlib/src ./run_host_test.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
UZLIB_SRC="${UZLIB_SRC:-$HOME/.platformio/packages/framework-arduinoespressif8266/tools/sdk/uzlib/src}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

gcc -O2 -Wall -I"$UZLIB_SRC" -o "$WORK/host_test" "$HERE/host_test.c" "$HERE/../src/frame_codec.c" \
  "$UZLIB_SRC/tinflate.c" "$UZLIB_SRC/tinfzlib.c" "$UZLIB_SRC/adler32.c" "$UZLIB_SRC/crc32.c"

python3 "$HERE/make_vectors.py" "$WORK"

fail=0
for raw in "$WORK"/*.raw; do
  name="$(basename "$raw" .raw)"
  for seed in 1 2 3; do
    printf '%-10s seed %s: ' "$name" "$seed"
    "$WORK/host_test" "$WORK/$name.w12" "$raw" 4096 "$seed" | tr '\n' ' ' || fail=1
    echo
  done
done

echo "--- expected failures"
first="$(ls "$WORK"/*.raw | head -1)"; name="$(basename "$first" .raw)"
# These runs are meant to fail, so capture their output instead of letting pipefail trip.
result="$("$WORK/host_test" "$WORK/$name.w15" "$first" 4096 1 || true)"
printf 'window too large (wbits 15, 4 KB dictionary): '
grep -q 'rc=-3' <<<"$result" && echo "rejected as expected" || { echo "NOT rejected"; fail=1; }
head -c 2000 "$WORK/$name.w12" > "$WORK/truncated"
result="$("$WORK/host_test" "$WORK/truncated" "$first" 4096 1 || true)"
printf 'truncated stream: '
grep -qE 'rc=-(1|2)' <<<"$result" && echo "rejected as expected" || { echo "NOT rejected"; fail=1; }

[ "$fail" = 0 ] && echo "ALL PASSED" || { echo "FAILED"; exit 1; }
