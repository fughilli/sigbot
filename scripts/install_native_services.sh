#!/usr/bin/env bash
# Install signal-cli + signal-cli-rest-api natively (no docker) into a prefix.
# Default prefix is <repo>/.deps — inside the workspace bind mount, so it
# survives dev-container restarts. Idempotent: done-markers skip finished steps.
#
# Why this exists: the dev container cannot run containers (seccomp blocks
# user namespaces, no docker socket), so the compose stack is replaced by
# native binaries + scripts/signal_api.py. On a box with docker, compose
# remains an equally supported path.
set -euo pipefail

PREFIX="${1:-$(cd "$(dirname "$0")/.." && pwd)/.deps}"

SIGNAL_CLI_VERSION=0.14.6
LIBSIGNAL_VERSION=0.96.3   # must match libsignal-client-*.jar inside signal-cli
REST_API_VERSION=0.100
GO_VERSION=1.26.5          # build toolchain only; not a runtime dep

case "$(uname -m)" in
  aarch64) JRE_ARCH=aarch64 GO_ARCH=arm64  LIBSIGNAL_ARCH=aarch64 ;;
  x86_64)  JRE_ARCH=x64     GO_ARCH=amd64  LIBSIGNAL_ARCH=x86_64  ;;
  *) echo "unsupported arch $(uname -m)"; exit 1 ;;
esac

mkdir -p "$PREFIX" && cd "$PREFIX"
mark() { touch ".done-$1"; }
done_() { [ -f ".done-$1" ]; }

# --- JRE 25 (Temurin) — signal-cli 0.14.x is built for class file 69 ----------
if ! done_ jre; then
  echo "== JRE 25 ($JRE_ARCH)"
  curl -sL -o jre.tar.gz \
    "https://api.adoptium.net/v3/binary/latest/25/ga/linux/${JRE_ARCH}/jre/hotspot/normal/eclipse"
  rm -rf jre && mkdir jre && tar xzf jre.tar.gz -C jre --strip-components=1
  rm jre.tar.gz && mark jre
fi

# --- signal-cli (JVM distribution) --------------------------------------------
if ! done_ signal-cli; then
  echo "== signal-cli $SIGNAL_CLI_VERSION"
  curl -sL -o signal-cli.tar.gz \
    "https://github.com/AsamK/signal-cli/releases/download/v${SIGNAL_CLI_VERSION}/signal-cli-${SIGNAL_CLI_VERSION}.tar.gz"
  rm -rf "signal-cli-${SIGNAL_CLI_VERSION}" signal-cli
  tar xzf signal-cli.tar.gz && mv "signal-cli-${SIGNAL_CLI_VERSION}" signal-cli
  rm signal-cli.tar.gz && mark signal-cli
fi

# --- libsignal JNI for non-x86_64 (upstream jar bundles x86_64 only) -----------
if [ "$LIBSIGNAL_ARCH" != "x86_64" ] && ! done_ libsignal; then
  echo "== libsignal_jni $LIBSIGNAL_VERSION ($LIBSIGNAL_ARCH) from exquo/signal-libs-build"
  curl -sL -o libsignal_jni.tar.gz \
    "https://github.com/exquo/signal-libs-build/releases/download/libsignal_v${LIBSIGNAL_VERSION}/libsignal_jni.so-v${LIBSIGNAL_VERSION}-${LIBSIGNAL_ARCH}-unknown-linux-gnu.tar.gz"
  tar xzf libsignal_jni.tar.gz   # -> libsignal_jni.so
  JAR="$(ls signal-cli/lib/libsignal-client-*.jar)"
  python3 - "$JAR" <<'EOF'
import shutil, sys, zipfile
jar = sys.argv[1]
shutil.copy(jar, jar + ".orig")
with zipfile.ZipFile(jar + ".orig") as src, \
     zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as dst:
    for item in src.infolist():
        if item.filename != "libsignal_jni.so":
            dst.writestr(item, src.read(item.filename))
    dst.write("libsignal_jni.so", "libsignal_jni.so")
print("replaced libsignal_jni.so in", jar)
EOF
  rm libsignal_jni.tar.gz libsignal_jni.so && mark libsignal
fi

# --- signal-cli-rest-api (build from source with a throwaway Go) ---------------
if ! done_ rest-api; then
  echo "== go $GO_VERSION (build toolchain)"
  curl -sL -o go.tar.gz "https://go.dev/dl/go${GO_VERSION}.linux-${GO_ARCH}.tar.gz"
  rm -rf go && tar xzf go.tar.gz && rm go.tar.gz

  echo "== signal-cli-rest-api $REST_API_VERSION"
  curl -sL -o rest-api.tar.gz \
    "https://github.com/bbernhard/signal-cli-rest-api/archive/refs/tags/${REST_API_VERSION}.tar.gz"
  rm -rf "signal-cli-rest-api-${REST_API_VERSION}"
  tar xzf rest-api.tar.gz && rm rest-api.tar.gz
  mkdir -p bin
  (
    cd "signal-cli-rest-api-${REST_API_VERSION}/src"
    export GOROOT="$PREFIX/go" GOPATH="$PREFIX/gopath" PATH="$PREFIX/go/bin:$PREFIX/gopath/bin:$PATH"
    go install github.com/swaggo/swag/cmd/swag@v1.16.4   # generates the docs pkg main.go imports
    swag init --requiredByDefault --outputTypes "go,json"
    go build -o "$PREFIX/bin/signal-cli-rest-api" main.go
  )
  rm -rf "signal-cli-rest-api-${REST_API_VERSION}" go gopath
  mark rest-api
fi

echo
echo "installed under $PREFIX:"
echo "  jre/                 (Temurin 25)"
echo "  signal-cli/          (v$SIGNAL_CLI_VERSION, libsignal ${LIBSIGNAL_ARCH})"
echo "  bin/signal-cli-rest-api (v$REST_API_VERSION)"
echo "next: scripts/signal_api.py start --mode normal"
