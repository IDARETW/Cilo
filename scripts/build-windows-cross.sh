#!/usr/bin/env bash
# Optional Windows x64 cross-build from Linux x64. For native Windows builds,
# use cargo build --release --locked with the Visual Studio C++ build tools.
set -euo pipefail

cilo_tools="${CILO_TOOLCHAINS:-/workspace/toolchains}"
cilo_version=20261020
cilo_archive="llvm-mingw-${cilo_version}-msvcrt-ubuntu-22.04-x86_64.tar.xz"
cilo_toolchain="$cilo_tools/${cilo_archive%.tar.xz}"
cilo_sha256=ceb8c5acc6f76e143f9c714772d2fa677027802561ce68c396e8dc990a80f0b6
mkdir -p "$cilo_tools"

if [[ ! -x "$cilo_toolchain/bin/x86_64-w64-mingw32-clang" ]]; then
    cilo_download_dir="$(mktemp -d /tmp/cilo-llvm-mingw.XXXXXX)"
    trap 'rm -rf "$cilo_download_dir"' EXIT
    curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
        "https://github.com/mstorsjo/llvm-mingw/releases/download/$cilo_version/$cilo_archive" \
        -o "$cilo_download_dir/$cilo_archive"
    (cd "$cilo_download_dir" && printf '%s  %s\n' "$cilo_sha256" "$cilo_archive" | sha256sum --check -)
    tar -xJf "$cilo_download_dir/$cilo_archive" -C "$cilo_tools"
fi

export PATH="$cilo_toolchain/bin:$PATH"
export CC_x86_64_pc_windows_gnullvm=x86_64-w64-mingw32-clang
export AR_x86_64_pc_windows_gnullvm=x86_64-w64-mingw32-ar
export CARGO_TARGET_X86_64_PC_WINDOWS_GNULLVM_LINKER=x86_64-w64-mingw32-clang
export CARGO_TARGET_X86_64_PC_WINDOWS_GNULLVM_RUSTFLAGS="${CARGO_TARGET_X86_64_PC_WINDOWS_GNULLVM_RUSTFLAGS:-} -C target-feature=+crt-static"
rustup target add x86_64-pc-windows-gnullvm
cargo build --release --locked --target x86_64-pc-windows-gnullvm
cargo test --no-run --locked --target x86_64-pc-windows-gnullvm
