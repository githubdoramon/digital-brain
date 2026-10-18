# Tryx history binding

This directory contains a minimal patch, the upstream MIT license, and immutable
source pins for `tryx==1.5.0+digitalbrain.1`. The package is built in the existing
orchestrator Dockerfile's build stage; Rust is not needed in the runtime image.
No additional deployed service/container is introduced.

`manifest.json` pins Tryx, its whatsapp-rust submodule, and the patch checksum.
The patch adds `AdvancedClient.fetch_message_history`, its Python type stub, and
a local package version. It corrects the upstream Cargo.lock root package version
(1.3.1 versus Cargo.toml's 1.5.0); dependency resolutions are unchanged. The native
method forwards to whatsapp-rust's existing PDO history implementation with
bounded/validated parameters and a safe generic Python exception on failure.
Do not send an ordinary user message or reimplement encrypted PDO framing in Python.

The pinned default Rust features are retained because the upstream wrapper imports
VoIP types unconditionally even with its default features disabled. No VoIP/media
method is called by Digital Brain's archive runtime.

For a host development environment with Rust 1.96, Git, a C compiler, and
`maturin==1.12.0` installed:

```sh
.venv/bin/python scripts/build_tryx_history.py \
  --source-dir /tmp/digital-brain-tryx-build \
  --wheel-dir /tmp/digital-brain-tryx-wheels
.venv/bin/python -m pip install --find-links=/tmp/digital-brain-tryx-wheels \
  'tryx==1.5.0+digitalbrain.1'
```

Use `--profile dev` for a faster host-only development wheel. Container builds use
the release profile, Python 3.11, and the runtime's Linux architecture. The build
script checks both revisions and patch hash, then builds with Cargo `--locked`.
The Docker build installs the produced wheel and asserts the added method exists.
The application requirements pin the local version, so an ordinary unpatched PyPI
wheel cannot silently replace it.

No build command links an account, reads production session data, or sends a
WhatsApp protocol request. Live backfill still requires account-level verification.
