# Installation details

Start with the [quick installation](../README.md#installation). The package
requires Nix 2.35+ and supports `x86_64-linux` and `aarch64-darwin`; the MCP
server requires Python 3.10+.

## Package and cache

The installer pins package version `1.0.0` at
[`0e0abf2aa6979e337aead8996983952dc1742530`](https://github.com/keplertech/kepler-formal/tree/0e0abf2aa6979e337aead8996983952dc1742530).
The [2026-09-11 publication run](https://github.com/keplertech/kepler-formal/actions/runs/34611550143)
verified public-cache downloads on both platforms. A direct macOS cache query
on 2026-09-13 confirmed
`/nix/store/2gc5ms1g8mb5shrjxk2gs76i0d3zvfz6-kepler-formal-1.0.0`.
Linux evidence here is publication CI, without a local Linux installation.

As of that date, newer upstream `main`
[`571ae59d39bd200de5b22d4727d9d00930311cff`](https://github.com/keplertech/kepler-formal/tree/571ae59d39bd200de5b22d4727d9d00930311cff)
contained additional checker changes without a successful Nix publication;
its macOS output was also unavailable in a direct cache check. The installer
uses the published release.

Cache settings and the public signing key apply only to the install command.
Signature checking remains enabled. Local/remote builds are disabled with
`--max-jobs 0 --builders ''`, as is import-from-derivation. Missing cached
packages or dependencies fail installation; the script does not compile or
edit Nix configuration. If a daemon rejects cache trust, follow the
[upstream Nix instructions](https://github.com/keplertech/kepler-formal/blob/0e0abf2aa6979e337aead8996983952dc1742530/README.md#nix--nixos)
with your administrator.

The Nix URL's `submodules=1` controls upstream package resolution. This
repository has no checker submodule; `install_kepler_formal.sh` replaces the
former build script and `--install-deps` option.

## Profiles and desktop clients

Installation defaults to the user's Nix profile. For another profile:

```bash
./install_kepler_formal.sh --profile /absolute/path/to/profile
export KEPLER_FORMAL_BINARY=/absolute/path/to/profile/bin/kepler-formal
```

The server uses `KEPLER_FORMAL_BINARY` when set, otherwise `kepler-formal` on `PATH`.
For a missing-checker error, test the exact executable with `--help`.
Use the Nix wrapper, which supplies its native and embedded Python runtime;
no checker source `PYTHONPATH` is needed. See
[desktop client configuration](instructions-claude.md).
