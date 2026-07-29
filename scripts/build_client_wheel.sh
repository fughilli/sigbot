#!/usr/bin/env bash
# Build dist/sigbot_client-*.whl for installing on the host or in another
# container. Uses system pip when present; falls back to the bazel-managed
# hermetic python + vendored pip (which exists in this dev container, where
# the system python has no pip).
set -euo pipefail
cd "$(dirname "$0")/.."

if python3 -m pip --version >/dev/null 2>&1; then
  exec python3 -m pip wheel --no-deps ./client -w dist/
fi

ext=$(ls -d "$HOME"/.cache/bazel/_bazel_*/*/external 2>/dev/null | head -1)
py=$(ls -d "$ext"/rules_python~~python~python_*/bin/python3 2>/dev/null | head -1)
[ -n "$py" ] || { echo "no pip and no bazel hermetic python — run 'bazel test //...' once first" >&2; exit 1; }
PYTHONPATH="$ext/rules_python~~config~pypi__pip:$ext/rules_python~~config~pypi__setuptools:$ext/rules_python~~config~pypi__wheel" \
  "$py" -m pip wheel --no-deps --no-build-isolation ./client -w dist/
