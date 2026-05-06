#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"
REPO_DIR="$ROOT_DIR/thirdparty/kepler-formal"
BUILD_DIR="$REPO_DIR/build"
INSTALL_DEPS=0

usage() {
  cat <<'EOF'
Usage: build_kepler_formal.sh [--install-deps]

  --install-deps   Install system dependencies for the current OS before building.
EOF
}

install_dependencies() {
  case "$(uname -s)" in
    Linux)
      if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y \
          g++ libboost-dev python3.9-dev capnproto libcapnp-dev libtbb-dev \
          pkg-config bison flex doxygen libspdlog-dev libfmt-dev \
          libboost-iostreams-dev zlib1g-dev cmake git
      elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y \
          gcc-c++ boost-devel python3-devel capnproto capnproto-devel \
          tbb-devel pkgconf-pkg-config bison flex doxygen spdlog-devel \
          fmt-devel boost-iostreams-devel zlib-devel cmake git
      else
        echo "Unsupported Linux package manager. Install the dependencies manually." >&2
        exit 1
      fi
      ;;
    Darwin)
      if ! command -v brew >/dev/null 2>&1; then
        echo "Homebrew is required on macOS to install dependencies." >&2
        exit 1
      fi
      brew install cmake doxygen capnp tbb bison flex boost spdlog zlib
      ;;
    *)
      echo "Automatic dependency installation is not configured for $(uname -s)." >&2
      exit 1
      ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-deps)
      INSTALL_DEPS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "$INSTALL_DEPS" -eq 1 ]]; then
  install_dependencies
fi

cpu_count() {
  if command -v nproc >/dev/null 2>&1; then
    nproc
  elif command -v sysctl >/dev/null 2>&1; then
    sysctl -n hw.ncpu
  else
    echo 1
  fi
}

git -C "$ROOT_DIR" submodule update --init --recursive -- thirdparty/kepler-formal

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_STANDARD=20 \
  -DCMAKE_CXX_FLAGS="-O3 -march=native -ffast-math -flto -DNDEBUG" \
  -DCMAKE_CXX_FLAGS_RELEASE="-Ofast -march=native -ffast-math -flto -DNDEBUG" \
  -DCMAKE_EXE_LINKER_FLAGS="-flto"
cmake --build . --parallel "$(cpu_count)"

echo "Build complete: $BUILD_DIR"