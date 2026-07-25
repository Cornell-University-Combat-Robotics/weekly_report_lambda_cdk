#!/usr/bin/env bash
#
# Assemble the Lambda deployment package into build/lambda/.
#
# lambda/ stays pure source. Dependencies are installed here instead, so the
# source tree never shadows your interpreter -- vendoring them next to the
# handlers meant anything run from lambda/ imported wheels built for the Lambda
# runtime's Python rather than the local one.
#
# Wheels are resolved for the Lambda runtime (3.11), not the machine running
# this script, so the package is correct regardless of your local Python.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/lambda"
OUT="$ROOT/build/lambda"
PYTHON="${LAMBDA_PYTHON:-python3}"
RUNTIME_VERSION="3.11"

rm -rf "$OUT"
mkdir -p "$OUT"

# Handlers only: no tests, no fixtures, no local credentials.
for handler in api.py dispatcher.py planner.py refresher.py; do
  cp "$SRC/$handler" "$OUT/"
done
cp "$SRC/requirements.txt" "$OUT/"

"$PYTHON" -m pip install \
  --requirement "$OUT/requirements.txt" \
  --target "$OUT" \
  --quiet --upgrade \
  --only-binary=:all: \
  --python-version "$RUNTIME_VERSION" \
  --implementation cp --abi none --platform any

# pip leaves console-script shims that a Lambda package has no use for.
rm -rf "$OUT/bin"

echo "built $OUT ($(du -sh "$OUT" | cut -f1))"
