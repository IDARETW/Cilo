# Cilo archive formats v1 and v2

Cilo 0.2 writes v2 and reads both versions. Cilo 0.1 cannot read v2.
The manifest field layout is unchanged; v2 adds reversible block filters.

All fixed integers are little-endian unless stated otherwise. The initial
64-byte header is followed by contiguous payload blocks, then one compressed
manifest. There are no padding bytes or trailing data.

| Header offset | Length | Meaning |
|---:|---:|---|
| 0 | 8 | `43 49 4c 4f VV 0d 0a 1a`, where `VV` is 01 or 02 |
| 8 | 8 | Manifest file offset |
| 16 | 8 | Compressed manifest length |
| 24 | 8 | Uncompressed manifest length |
| 32 | 32 | BLAKE3 of the uncompressed manifest |

The manifest is Zstandard-compressed bincode **1.3 fixed-integer** encoding of
`Manifest` in `src/lib.rs`, using declared field order. Integers are
little-endian, sequence/string lengths are u64, strings are UTF-8, and hashes are
32 raw bytes. The version field must match the header's version byte. The decoder
rejects trailing metadata.

The manifest records directories, files, unique chunks, and solid blocks. File
entries hold relative forward-slash paths, total size, whole-file BLAKE3, ordered
u32 chunk references, signed Unix modification seconds, u32 nanoseconds, and a
boolean executable flag. Chunk records contain u32 block index, offset, length,
and BLAKE3. Block records contain u64 file offset, u32 stored/raw length, u8 codec
(see below), and BLAKE3 of original, unfiltered bytes.

| Codec | Versions | Stored representation |
|---:|---|---|
| 0 | 1, 2 | Original bytes |
| 1 | 1, 2 | Zstandard frame of original bytes |
| 2 | 2 | Zstandard frame after x86 branch normalization |
| 3 | 2 | Zstandard frame after 2-byte lane shuffling |
| 4 | 2 | Zstandard frame after 4-byte lane shuffling |

Codec 2 scans from index zero. If the current byte is E8 or E9 and at least five
bytes remain, interpret the next four as a little-endian u32, add `index + 5`
modulo 2^32, and advance five bytes. Otherwise advance one. Decode performs the
same scan and subtracts instead. This is a byte transform, not disassembly.

For codecs 3/4, let width be 2/4 and `count = floor(raw_len / width)`. Encoding
maps `input[i * width + lane]` to `output[lane * count + i]`. The final
`raw_len % width` bytes stay at their original positions. Decoding reverses this
mapping. Each transform is local to one block, so there are no reference chains.

All Zstandard frames decode to exactly `raw_len` bytes. The filter is reversed
before block/chunk/file checksum checks. Stored bytes are never larger than
the raw block; plain Zstandard and raw storage remain alternatives. Format
detection, alignment hints and encoder trial decisions are not needed to decode.

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
`verify` and `unpack`. A unique chunk's digest is checked on its first use;
every newly decoded block and every logical file is still hashed. Reusing a
verified chunk does not skip block validation after cache eviction. `list` only
validates the header and manifest.

Integrity checks are unkeyed. This format neither encrypts nor authenticates
against an attacker who can alter both content and its checksums.
