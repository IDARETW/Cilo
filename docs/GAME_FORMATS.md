# Game format analysis and compression modules

Cilo 0.2 identifies file contents, validates container bounds, and uses the
resulting physical regions as chunk boundaries. FastCDC still splits large
regions. Unknown revisions, overlapping entries, truncated tables and unsupported
layouts fall back to ordinary FastCDC; their bytes can still be archived.
No game executable or driver is executed, and no game codec DLL is required.

Run `cilo analyze PATH` to see the detected format, region end offsets, filter
class, and any fallback reason. `cilo pack ... --generic` disables the layout
parsers and filters for comparisons. It retains whole-file/chunk deduplication
and the compression pipeline.

## Implemented modules

| Files | Validated layout | Archiving treatment |
|---|---|---|
| `.exe`, `.dll`, `.sys` | MZ/PE signatures, x86/x64 machine, optional header, bounded nonoverlapping raw sections | Separate code and numeric sections. Trial reversible E8/E9 normalization in executable sections; trial 2/4-byte shuffling in `.data` and `.pdata`. Keep the smaller actual encoding. Preserve headers, signatures and overlays. |
| `.ff` | `IWffa100`, version 11, 136-byte header, secure stream wrapper | Identify physical Oodle block boundaries. Keep authentication pages, compressed payload, headers and padding intact. |
| `.fp` | `IWffd100`, version 6, 328-byte header with two embedded fastfile headers | Same secure-stream parser; archive the original patch bytes without applying the patch. |
| `.sabs`, `.sabl` | Magic `0x23585532`, version 10, 44-byte entries | Bound each seek table and audio payload separately. Reordered or shared audio can deduplicate independently of its bank offset. Preserve the index and other metadata. |
| `.xpak` | `KAPI`, version 13, extended header, 24-byte hash-index entries | Bound each stored object independently, masking the high flag byte of its size. Keep native compression and indexes intact. |
| `.bik` | Bink 1 b/f/g/h/i/k; Bink 2 i/j/k; audio tables and ordered frame offsets | Bound frames independently, preserving encoded audio/video. The sampled MW2019 video is **KB2j (Bink 2)** despite its `.bik` extension. |
| Other files/revisions | No trusted layout | Ordinary content-defined deduplication and solid Zstandard. |

Real samples validated the MW2019 FF/FP revisions above, SAB v10, XPAK v13,
x64 PE, and KB2j. Other supported Bink revisions/audio-table variants have
synthetic layout tests, not a claim of validation across every game release.
Layout validation does not validate the game's internal codec bitstream; Cilo
does not need to decode it to preserve it.

## What is inside these files

The signed fastfiles start their stream wrapper after 32 KiB following the
136/328-byte file header. The wrapper magic is `0x43574902`. Data windows are
8 MiB, with 16 KiB authentication pages between them. Compressed blocks have
three u32 header fields: stored size, decoded size, and flags. Payloads have
four-byte alignment. The sampled blocks predominantly decode to 64 KiB through
Oodle. Cilo checks the declared total decoded size without decompressing any
block. Hash pages can interrupt even a block header; their bytes are retained.

The sample sound banks have 650 and 28 entries. Their observed format bytes
are 14 and 8. Existing MW2019 tooling treats the v10 payloads as compressed
audio, including headerless FLAC handling. Cilo deliberately treats each audio
payload as opaque; it does not make a blanket codec claim from an entry byte.
Re-encoding audio or video would not generally reproduce the original bytes.

The XPAK v13 header inserts 288 bytes after the first 24 bytes of the older
layout. Its hash entries give a relative data offset and a flagged stored size.
Game tooling supports raw, LZ4 and Oodle command streams inside these objects.
This implementation segments at the **object** level. It does not claim to
parse every internal XPAK command stream or transcode textures.

The downloaded 289 MiB `modernwarfare.exe` has two large executable sections
that compress very little. Executable extensions do not guarantee ordinary,
compressible machine code. Cilo's branch-filter probe skips unpromising regions,
and the final encoder compares trial output with plain Zstandard and raw bytes.
The DLSS DLL's code and numeric sections were more productive transform targets.

## Ratio and speed choices

- Chunk identities and all integrity hashes cover **original bytes**. Filters
  run after deduplication and reverse before integrity checks. Numeric chunk
  boundaries retain four-byte alignment so omitted duplicates cannot mix lanes.
- A complete BLAKE3 match allows an identical file to reuse its existing chunk
  list. A matching size alone never establishes identity. Same-size misses are
  read again for normal chunking; this can cost an extra pass on such inputs.
- A bounded worker queue overlaps reading, hashing and independent compression.
  At most `2 × threads` blocks are in flight, plus the current block. There are
  at most `threads` active compressor workspaces. Output remains ordered and
  deterministic across thread counts.
- The default solid block grows from 32 to 64 MiB. Decode windows remain bounded
  at 64 MiB. Use `--block-mib 32 --threads 2` on a machine with limited memory.
  Raw queued blocks alone can approach 512 MiB at the default four workers;
  compressor workspaces, trial buffers, metadata and dedup indexes add to that.
- The decoder hashes each unique chunk once, each newly decoded block, and
  every reconstructed file. It never trusts an unverified cached block.
- Tested alternatives included 16/64/256 KiB CDC targets, mixed filtered solid
  blocks, whole-section splitting, and prior-block Zstandard dictionaries.
  Smaller chunks were not consistently smaller archives. Dictionary trials
  saved little while adding reference dependencies, so they were not shipped.
  Fully separating PE sections plus 64 MiB generic blocks performed better on
  this corpus than embedding filtered ranges in shared blocks.

These are format-specific **lossless preprocessing modules**, not new Oodle,
FLAC or Bink codecs. Lossless 80% savings cannot be promised for a single build
dominated by already compressed or protected bytes. Collections of related
builds can cross that threshold by storing shared content once. See the
[measured before/after results](../benchmarks/REFINEMENT.md), including regressions.

## Layout references

The parsers are Cilo implementations informed by public format descriptions
and independently checked against downloaded bytes:

- [Microsoft PE/COFF specification](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format).
- [Cordycep MW2019 loader](https://github.com/Scobalula/Cordycep/blob/3be1e21221c61f3f021346996f4b17586e18ad4d/src/Parasyte/CoDMW4Handler.cpp)
  and [secure Oodle stream layout](https://github.com/Scobalula/Cordycep/blob/3be1e21221c61f3f021346996f4b17586e18ad4d/src/Parasyte/OodleDecompressorV1.cpp).
- Greyhound [SAB/XPAK structures](https://github.com/Scobalula/Greyhound/blob/22c3d17f7428573042b56f43af0ed2585989bebd/src/WraithXCOD/WraithXCOD/DBGameFiles.h),
  [SAB handling](https://github.com/Scobalula/Greyhound/blob/22c3d17f7428573042b56f43af0ed2585989bebd/src/WraithXCOD/WraithXCOD/SABSupport.cpp),
  and [XPAK v13 handling](https://github.com/Scobalula/Greyhound/blob/22c3d17f7428573042b56f43af0ed2585989bebd/src/WraithXCOD/WraithXCOD/XPAKCache.cpp).
- [FFmpeg Bink demuxer](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/bink.c)
  and [Bink container description](https://wiki.multimedia.cx/index.php/Bink_Container).
