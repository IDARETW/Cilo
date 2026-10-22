#!/usr/bin/env bash
# Reusable setup for the managed Linux cloud workspace. Native builds only;
# optional cross-compilers and multi-gigabyte game samples are not installed.
set -euo pipefail

cilo_repo="${1:-/workspace/Cilo}"
export RUSTUP_HOME=/workspace/toolchains/rustup
export CARGO_HOME=/workspace/toolchains/cargo
export PATH="$CARGO_HOME/bin:$PATH"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-4}"

for cilo_command in cc c++ curl sha256sum python3 node; do
    command -v "$cilo_command" >/dev/null || {
        echo "Missing prerequisite: $cilo_command" >&2
        exit 1
    }
done

if [[ ! -x "$CARGO_HOME/bin/rustup" ]]; then
    cilo_download_dir="$(mktemp -d /tmp/cilo-rustup.XXXXXX)"
    trap 'rm -rf "$cilo_download_dir"' EXIT
    cilo_rustup_url=https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init
    curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
        "$cilo_rustup_url" -o "$cilo_download_dir/rustup-init"
    curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
        "$cilo_rustup_url.sha256" -o "$cilo_download_dir/rustup-init.sha256"
    (cd "$cilo_download_dir" && sha256sum --check rustup-init.sha256)
    chmod +x "$cilo_download_dir/rustup-init"
    "$cilo_download_dir/rustup-init" -y --no-modify-path --profile minimal \
        --default-toolchain 1.90.0
fi

rustup toolchain install 1.90.0 --profile minimal --component rustfmt --component clippy
cd "$cilo_repo"
cargo fetch --locked
cargo fmt --all -- --check
cargo clippy --all-targets --locked -- -D warnings
cargo test --locked
cargo build --release --locked
node --test tools/casc.test.mjs
python3 -m unittest discover -s tests -p 'test_*.py' -v
