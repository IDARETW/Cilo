# Cilo archive format v1

All fixed integers are little-endian unless stated otherwise. The initial
64-byte header is followed by contiguous payload blocks, then one compressed
manifest. There are no padding bytes or trailing data.

| Header offset | Length | Meaning |
|---:|---:|---|
| 0 | 8 | `43 49 4c 4f 01 0d 0a 1a` |
| 8 | 8 | Manifest file offset |
| 16 | 8 | Compressed manifest length |
| 24 | 8 | Uncompressed manifest length |
| 32 | 32 | BLAKE3 of the uncompressed manifest |

The manifest is Zstandard-compressed bincode **1.3 fixed-integer** encoding of
`Manifest` in `src/lib.rs`, using declared field order. Integers are
little-endian, sequence/string lengths are u64, strings are UTF-8, and hashes are
32 raw bytes. The version field is 1. The decoder rejects trailing metadata.

The manifest records directories, files, unique chunks, and solid blocks. File
entries hold relative forward-slash paths, total size, whole-file BLAKE3, ordered
u32 chunk references, signed Unix modification seconds, u32 nanoseconds, and a
boolean executable flag. Chunk records contain u32 block index, offset, length,
and BLAKE3. Block records contain u64 file offset, u32 stored/raw length, u8 codec
(0 = raw, 1 = Zstandard), and BLAKE3 of decoded bytes.

Each file is reconstructed by concatenating its referenced chunks. Duplicate
content refers to the original chunk, including when insertions shift later
content. Unique chunks cover each decoded block exactly once without gaps or
overlap. Blocks cover the payload region exactly once. Empty files have no
chunks and carry the BLAKE3 digest of the empty string.

Limits: blocks and decoding windows are at most 64 MiB; the raw and stored
manifest are at most 512 MiB. Block lengths, contiguous offsets, chunk bounds,
all references, path hierarchy and uniqueness, file sizes, and total-size
overflow are checked before restoration. A file/chunk reference may repeat;
an unreferenced chunk is rejected. All referenced payload is verified by
`verify` and `unpack`. `list` only validates the header and manifest.

Integrity checks are unkeyed. This format neither encrypts nor authenticates
against an attacker who can alter both content and its checksums.
