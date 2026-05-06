# kepler-formal-mcp

## Usage

To use this MCP, simply:

1. Clone the repository with submodules, or initialize the submodule afterward.
2. Run the build script:

```bash
./build_kepler_formal.sh
```

If you also want the system dependencies to be installed automatically, run:

```bash
./build_kepler_formal.sh --install-deps
```

This script will fetch `kepler-formal`, initialize its submodules, and then build the binary in `thirdparty/kepler-formal/build/`.

The repository now includes `kepler-formal` as a proper Git submodule in `thirdparty/kepler-formal`.

After that, `server.py` automatically looks for the generated binary in the correct directory, so there is no extra manual step to locate it.

## Dependencies

On Linux Ubuntu/Debian:

```bash
sudo apt-get install g++ libboost-dev python3.9-dev capnproto libcapnp-dev libtbb-dev pkg-config bison flex doxygen libspdlog-dev libfmt-dev libboost-iostreams-dev zlib1g-dev
```

On Fedora:

```bash
sudo dnf install gcc-c++ boost-devel python3-devel capnproto capnproto-devel tbb-devel pkgconf-pkg-config bison flex doxygen spdlog-devel fmt-devel boost-iostreams-devel zlib-devel cmake git
```

On macOS with Homebrew:

```bash
brew install cmake doxygen capnp tbb bison flex boost spdlog zlib
```

On Windows, the easiest approach is to use WSL2 or MSYS2 with a compatible Bash environment. Automatic installation of system dependencies is not supported on native Windows.