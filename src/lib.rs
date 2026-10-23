//! Byte-exact game archives with container-aware deduplication and reversible filters.
mod filters;
pub mod formats;
pub mod stream;
use anyhow::{Context, Result, bail, ensure};
use bincode::Options;
use clap::ValueEnum;
use serde::{Deserialize, Serialize};
use std::{
    collections::{HashMap, HashSet, VecDeque},
    fs::{self, File, OpenOptions},
    io::{BufReader, Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::mpsc::{Receiver, channel},
    time::Instant,
};

const MAGIC_V1: &[u8; 8] = b"CILO\x01\r\n\x1a";
const MAGIC: &[u8; 8] = b"CILO\x02\r\n\x1a";
const HEADER: u64 = 64;
const MIB: usize = 1024 * 1024;
const MAX_BLOCK: usize = 64 * MIB;
const MAX_MANIFEST: usize = 512 * MIB;
type Digest = [u8; 32];
type EncodedBlock = (Vec<u8>, u8, Digest, u32);

#[derive(Clone, Copy, Debug, ValueEnum)]
pub enum Profile {
    Fast,
    Balanced,
    Max,
}

impl Profile {
    fn level(self) -> i32 {
        match self {
            Self::Fast => 3,
            Self::Balanced => 15,
            Self::Max => 22,
        }
    }
}

pub fn default_threads() -> usize {
    std::thread::available_parallelism().map_or(2, |n| n.get().min(4))
}

#[derive(Clone, Copy)]
pub struct PackOptions {
    pub profile: Profile,
    pub threads: usize,
    pub block_mib: usize,
    pub chunk_kib: u32,
    pub dedup: bool,
    pub format_aware: bool,
}

impl Default for PackOptions {
    fn default() -> Self {
        Self {
            profile: Profile::Balanced,
            threads: default_threads(),
            block_mib: 64,
            chunk_kib: 256,
            dedup: true,
            format_aware: true,
        }
    }
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Block {
    pub offset: u64,
    pub stored_len: u32,
    pub raw_len: u32,
    /// 0 = stored, 1 = Zstandard, 2 = branch filter, 3/4 = shuffle 2/4 + Zstandard.
    pub codec: u8,
    pub hash: Digest,
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Chunk {
    pub block: u32,
    pub offset: u32,
    pub len: u32,
    pub hash: Digest,
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Entry {
    pub path: String,
    pub size: u64,
    pub hash: Digest,
    pub chunks: Vec<u32>,
    pub modified_seconds: i64,
    pub modified_nanos: u32,
    pub executable: bool,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct Manifest {
    pub version: u32,
    pub directories: Vec<String>,
    pub files: Vec<Entry>,
    pub chunks: Vec<Chunk>,
    pub blocks: Vec<Block>,
}

#[derive(Serialize, Debug)]
pub struct Stats {
    pub operation: String,
    pub files: usize,
    pub input_bytes: u64,
    pub unique_bytes: u64,
    pub archive_bytes: u64,
    /// Original bytes divided by archive bytes. Larger is better.
    pub compression_ratio: f64,
    pub space_saved_percent: f64,
    pub elapsed_seconds: f64,
    /// Logical original bytes / wall time, including hashing and I/O.
    pub throughput_mib_s: f64,
    pub blocks_decoded: u64,
    pub filtered_blocks: usize,
}

impl Stats {
    fn new(op: &str, m: &Manifest, size: u64, start: Instant, decoded: u64) -> Self {
        let input: u64 = m.files.iter().map(|f| f.size).sum();
        let elapsed = start.elapsed().as_secs_f64();
        Self {
            operation: op.into(),
            files: m.files.len(),
            input_bytes: input,
            unique_bytes: m.chunks.iter().map(|c| c.len as u64).sum(),
            archive_bytes: size,
            compression_ratio: input as f64 / size.max(1) as f64,
            space_saved_percent: if input == 0 {
                0.0
            } else {
                100.0 * (1.0 - size as f64 / input as f64)
            },
            elapsed_seconds: elapsed,
            throughput_mib_s: input as f64 / MIB as f64 / elapsed.max(1e-9),
            blocks_decoded: decoded,
            filtered_blocks: m.blocks.iter().filter(|b| b.codec >= 2).count(),
        }
    }
}

fn portable_path(path: &str) -> Result<()> {
    ensure!(
        !path.is_empty() && path.len() <= 32760,
        "invalid path length"
    );
    for part in path.split('/') {
        ensure!(
            !part.is_empty() && part != "." && part != "..",
            "unsafe path: {path}"
        );
        ensure!(!part.ends_with([' ', '.']), "nonportable path: {path}");
        ensure!(
            !part.chars().any(|c| c < ' ' || "\\:<>\"|?*".contains(c)),
            "nonportable path: {path}"
        );
        let stem = part.split('.').next().unwrap_or("").to_uppercase();
        let device = matches!(
            stem.as_str(),
            "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$"
        ) || ["COM", "LPT"].iter().any(|p| {
            stem.strip_prefix(p).is_some_and(|s| {
                matches!(
                    s,
                    "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³"
                )
            })
        });
        ensure!(!device, "Windows reserved path: {path}");
    }
    Ok(())
}

fn relative_name(root: &Path, p: &Path) -> Result<String> {
    let mut parts = Vec::new();
    for part in p.strip_prefix(root)?.components() {
        let s = part
            .as_os_str()
            .to_str()
            .context("non-UTF-8 filenames are unsupported")?;
        // Backslashes in Unix filenames must not silently become directories on Windows.
        ensure!(!s.contains('\\'), "backslash in filename");
        parts.push(s);
    }
    let name = parts.join("/");
    portable_path(&name)?;
    Ok(name)
}

struct BlockWriter<'a> {
    file: &'a mut File,
    blocks: Vec<Block>,
    pending: VecDeque<Receiver<Result<EncodedBlock>>>,
    current: Vec<u8>,
    current_kind: formats::Kind,
    options: PackOptions,
    pool: rayon::ThreadPool,
}

impl<'a> BlockWriter<'a> {
    fn new(file: &'a mut File, options: PackOptions) -> Result<Self> {
        Ok(Self {
            file,
            blocks: Vec::new(),
            pending: VecDeque::new(),
            current: Vec::new(),
            current_kind: formats::Kind::Generic,
            options,
            pool: rayon::ThreadPoolBuilder::new()
                .num_threads(options.threads)
                .build()?,
        })
    }

    fn flush_pending(&mut self) -> Result<()> {
        while !self.pending.is_empty() {
            self.write_next()?;
        }
        Ok(())
    }

    fn write_next(&mut self) -> Result<()> {
        let result = self
            .pending
            .pop_front()
            .context("missing compression job")?;
        let (bytes, codec, hash, raw_len) =
            result.recv().context("compression worker stopped")??;
        let offset = self.file.stream_position()?;
        self.file.write_all(&bytes)?;
        self.blocks.push(Block {
            offset,
            stored_len: bytes.len() as u32,
            raw_len,
            codec,
            hash,
        });
        Ok(())
    }

    fn finish_current(&mut self) -> Result<()> {
        if !self.current.is_empty() {
            let raw = std::mem::take(&mut self.current);
            let kind = self.current_kind;
            let level = self.options.profile.level();
            let (sender, receiver) = channel();
            self.pool.spawn(move || {
                let _ = sender.send(encode_block(raw, kind, level));
            });
            self.pending.push_back(receiver);
        }
        // Bounded queue overlaps reads, hashing and compression. A second wave
        // prevents small PE sections from stalling all workers behind one block.
        if self.pending.len() >= self.options.threads * 2 {
            self.write_next()?;
        }
        Ok(())
    }

    fn push(&mut self, data: &[u8], hash: Digest, kind: formats::Kind) -> Result<Chunk> {
        if self.current_kind != kind
            || self.current.len() + data.len() > self.options.block_mib * MIB
        {
            self.finish_current()?;
        }
        self.current_kind = kind;
        let block =
            u32::try_from(self.blocks.len() + self.pending.len()).context("too many blocks")?;
        let chunk = Chunk {
            block,
            offset: self.current.len() as u32,
            len: data.len() as u32,
            hash,
        };
        self.current.extend_from_slice(data);
        Ok(chunk)
    }

    fn finish(mut self) -> Result<Vec<Block>> {
        self.finish_current()?;
        self.flush_pending()?;
        Ok(self.blocks)
    }
}

fn encode_block(raw: Vec<u8>, kind: formats::Kind, level: i32) -> Result<EncodedBlock> {
    let mut compressor = zstd::bulk::Compressor::new(level)?;
    use zstd::zstd_safe::CParameter;
    compressor.set_parameter(CParameter::WindowLog(26))?;
    compressor.set_parameter(CParameter::ChecksumFlag(true))?;
    compressor.set_parameter(CParameter::EnableLongDistanceMatching(true))?;
    let mut compressed = compressor.compress(&raw)?;
    let mut codec = 1;
    let candidates = if kind == formats::Kind::Numeric
        && compressed.len() * 10 > raw.len()
        && compressed.len() * 10 < raw.len() * 9
    {
        vec![3, 4]
    } else {
        filters::candidate(&raw, kind)?.into_iter().collect()
    };
    for filter in candidates {
        let trial = compressor.compress(&filters::apply(&raw, filter, false))?;
        if trial.len() < compressed.len() {
            compressed = trial;
            codec = filter;
        }
    }
    let hash = *blake3::hash(&raw).as_bytes();
    let len = raw.len() as u32;
    if compressed.len() < raw.len() {
        Ok((compressed, codec, hash, len))
    } else {
        Ok((raw, 0, hash, len))
    }
}

// Keep numeric words aligned when CDC chunks are deduplicated out of a solid
// block. Otherwise removing an odd-length chunk mixes the byte-shuffle lanes.
fn aligned_piece(
    data: &[u8],
    kind: formats::Kind,
    tail: &mut Vec<u8>,
    consume: &mut impl FnMut(&[u8], formats::Kind) -> Result<()>,
) -> Result<()> {
    if kind != formats::Kind::Numeric {
        return consume(data, kind);
    }
    tail.extend_from_slice(data);
    let aligned = tail.len() & !3;
    if aligned > 0 {
        consume(&tail[..aligned], kind)?;
    }
    let remainder = tail[aligned..].to_vec();
    tail.clear();
    tail.extend_from_slice(&remainder);
    Ok(())
}

/// Pack a directory without changing source files or replacing an existing archive.
pub fn pack(source: &Path, archive: &Path, options: PackOptions) -> Result<Stats> {
    let start = Instant::now();
    ensure!((1..=64).contains(&options.threads), "threads must be 1..64");
    ensure!(
        (4..=64).contains(&options.block_mib),
        "block-mib must be 4..64"
    );
    ensure!(
        (16..=1024).contains(&options.chunk_kib) && options.chunk_kib.is_power_of_two(),
        "chunk-kib must be a power of two in 16..1024"
    );
    ensure!(
        fs::symlink_metadata(source)?.file_type().is_dir(),
        "source must be a directory, not a symlink"
    );
    let root = source.canonicalize()?;
    let parent = archive
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."))
        .canonicalize()?;
    ensure!(
        !parent.starts_with(&root),
        "archive must be outside the source directory"
    );
    let destination = parent.join(archive.file_name().context("missing archive filename")?);
    ensure!(
        fs::symlink_metadata(&destination).is_err_and(|e| e.kind() == std::io::ErrorKind::NotFound),
        "archive destination already exists or is inaccessible"
    );
    let mut manifest = Manifest {
        version: 2,
        directories: Vec::new(),
        files: Vec::new(),
        chunks: Vec::new(),
        blocks: Vec::new(),
    };
    let mut sources: Vec<(String, PathBuf)> = Vec::new();
    let mut names = HashSet::new();
    for entry in walkdir::WalkDir::new(&root)
        .follow_links(false)
        .min_depth(1)
    {
        let entry = entry?;
        let name = relative_name(&root, entry.path())?;
        ensure!(
            names.insert(name.to_lowercase()),
            "case-insensitive filename collision: {name}"
        );
        if entry.file_type().is_dir() {
            manifest.directories.push(name);
        } else if entry.file_type().is_file() {
            sources.push((name, entry.path().into()));
        } else {
            bail!("symlink or special file is unsupported: {name}");
        }
    }
    manifest.directories.sort();
    // Group related file types into the same solid blocks without changing restored paths.
    sources.sort_by(|a, b| {
        Path::new(&a.0)
            .extension()
            .cmp(&Path::new(&b.0).extension())
            .then_with(|| {
                Path::new(&a.0)
                    .file_name()
                    .cmp(&Path::new(&b.0).file_name())
            })
            .then_with(|| a.0.cmp(&b.0))
    });
    pack_sources(
        sources.into_iter().map(Ok),
        manifest,
        parent,
        destination,
        options,
        start,
        None,
    )
}

fn check_cancelled(cancel: Option<&Path>) -> Result<()> {
    if let Some(path) = cancel {
        ensure!(!path.try_exists()?, "archive creation cancelled");
    }
    Ok(())
}

fn pack_sources(
    sources: impl IntoIterator<Item = Result<(String, PathBuf)>>,
    mut manifest: Manifest,
    parent: PathBuf,
    destination: PathBuf,
    options: PackOptions,
    start: Instant,
    cancel: Option<&Path>,
) -> Result<Stats> {
    let mut temp = tempfile::NamedTempFile::new_in(&parent)?;
    temp.write_all(&[0; HEADER as usize])?;
    let mut writer = BlockWriter::new(temp.as_file_mut(), options)?;
    let mut seen: HashMap<Digest, u32> = HashMap::new();
    let mut file_sizes = HashSet::new();
    let mut seen_files: HashMap<(u64, Digest), usize> = HashMap::new();
    let mut hash_buffer = vec![0; 256 * 1024];
    let avg = options.chunk_kib * 1024;
    let mut small_buffer = Vec::new();
    let mut alignment_tail = Vec::new();
    let mut directories: HashSet<String> = manifest.directories.iter().cloned().collect();
    let mut occupied: HashSet<String> = directories.iter().map(|s| s.to_lowercase()).collect();
    for source in sources {
        check_cancelled(cancel)?;
        let (name, path) = source?;
        portable_path(&name)?;
        ensure!(
            occupied.insert(name.to_lowercase()),
            "duplicate archive path: {name}"
        );
        let mut ancestor = name.as_str();
        while let Some((parent, _)) = ancestor.rsplit_once('/') {
            if directories.insert(parent.to_owned()) {
                ensure!(
                    occupied.insert(parent.to_lowercase()),
                    "conflicting archive directory: {parent}"
                );
            }
            ancestor = parent;
        }
        ensure!(
            fs::symlink_metadata(&path)?.is_file(),
            "source changed type: {name}"
        );
        let mut file = File::open(&path).with_context(|| format!("opening {name}"))?;
        let before = file.metadata()?;
        let modified = filetime::FileTime::from_last_modification_time(&before);
        let mut hash = blake3::Hasher::new();
        let mut chunks = Vec::new();
        let mut size = 0u64;
        // A length match is only a reason to hash; reuse requires the entire file digest.
        // This avoids parsing and chunking identical assets again for every build.
        let duplicate = if options.dedup && file_sizes.contains(&before.len()) {
            loop {
                let n = file.read(&mut hash_buffer)?;
                if n == 0 {
                    break;
                }
                hash.update(&hash_buffer[..n]);
                size += n as u64;
            }
            seen_files
                .get(&(size, *hash.finalize().as_bytes()))
                .copied()
        } else {
            None
        };
        if let Some(index) = duplicate {
            chunks.clone_from(&manifest.files[index].chunks);
        } else {
            hash = blake3::Hasher::new();
            size = 0;
            let plan = formats::plan(&mut file, before.len(), options.format_aware)?;
            let mut consume = |data: &[u8], kind: formats::Kind| -> Result<()> {
                hash.update(data);
                size = size
                    .checked_add(data.len() as u64)
                    .context("file size overflow")?;
                let digest = *blake3::hash(data).as_bytes();
                let existing = if options.dedup {
                    seen.get(&digest).copied()
                } else {
                    None
                };
                let id = match existing {
                    Some(id) => {
                        ensure!(
                            manifest.chunks[id as usize].len as usize == data.len(),
                            "chunk hash collision"
                        );
                        id
                    }
                    None => {
                        let id = u32::try_from(manifest.chunks.len()).context("too many chunks")?;
                        manifest.chunks.push(writer.push(data, digest, kind)?);
                        if options.dedup {
                            seen.insert(digest, id);
                        }
                        id
                    }
                };
                chunks.push(id);
                Ok(())
            };
            let mut input = BufReader::with_capacity(256 * 1024, file);
            let mut position = 0;
            for region in plan.regions {
                let len = region.end - position;
                if len <= (avg * 4) as u64 {
                    small_buffer.resize(len as usize, 0);
                    input
                        .read_exact(&mut small_buffer)
                        .with_context(|| format!("reading {name}"))?;
                    for piece in fastcdc::v2020::FastCDC::new(&small_buffer, avg / 4, avg, avg * 4)
                    {
                        aligned_piece(
                            &small_buffer[piece.offset..piece.offset + piece.length],
                            region.kind,
                            &mut alignment_tail,
                            &mut consume,
                        )?;
                    }
                } else {
                    let limited = (&mut input).take(len);
                    let mut stream = fastcdc::v2020::StreamCDC::new(limited, avg / 4, avg, avg * 4);
                    for piece in &mut stream {
                        let piece = piece.with_context(|| format!("reading {name}"))?;
                        aligned_piece(&piece.data, region.kind, &mut alignment_tail, &mut consume)?;
                    }
                }
                if !alignment_tail.is_empty() {
                    consume(&alignment_tail, region.kind)?;
                    alignment_tail.clear();
                }
                position = region.end;
            }
        }
        let after = fs::metadata(&path)?;
        ensure!(
            size == before.len()
                && size == after.len()
                && before.modified()? == after.modified()?,
            "source changed while archiving: {name}"
        );
        #[cfg(unix)]
        let executable = {
            use std::os::unix::fs::PermissionsExt;
            before.permissions().mode() & 0o111 != 0
        };
        #[cfg(not(unix))]
        let executable = false;
        let digest = *hash.finalize().as_bytes();
        if options.dedup {
            file_sizes.insert(size);
            seen_files.insert((size, digest), manifest.files.len());
        }
        manifest.files.push(Entry {
            path: name,
            size,
            hash: digest,
            chunks,
            modified_seconds: modified.unix_seconds(),
            modified_nanos: modified.nanoseconds(),
            executable,
        });
    }
    manifest.blocks = writer.finish()?;
    check_cancelled(cancel)?;
    manifest.directories = directories.into_iter().collect();
    manifest.directories.sort();
    let manifest_offset = temp.stream_position()?;
    let raw = bincode::serialize(&manifest)?;
    ensure!(
        raw.len() <= MAX_MANIFEST,
        "manifest exceeds 512 MiB; split the collection or increase chunk size"
    );
    let compressed = zstd::bulk::compress(&raw, 3)?;
    temp.write_all(&compressed)?;
    temp.seek(SeekFrom::Start(0))?;
    temp.write_all(MAGIC)?;
    temp.write_all(&manifest_offset.to_le_bytes())?;
    temp.write_all(&(compressed.len() as u64).to_le_bytes())?;
    temp.write_all(&(raw.len() as u64).to_le_bytes())?;
    temp.write_all(blake3::hash(&raw).as_bytes())?;
    temp.as_file().sync_all()?;
    let size = temp.as_file().metadata()?.len();
    check_cancelled(cancel)?;
    temp.persist_noclobber(&destination)
        .map_err(|e| e.error)
        .context("publishing archive")?;
    Ok(Stats::new("pack", &manifest, size, start, 0))
}

fn decompress(bytes: &[u8], raw_len: usize) -> Result<Vec<u8>> {
    let mut decoder = zstd::bulk::Decompressor::new()?;
    decoder.set_parameter(zstd::zstd_safe::DParameter::WindowLogMax(26))?;
    let raw = decoder.decompress(bytes, raw_len)?;
    ensure!(raw.len() == raw_len, "decompressed length mismatch");
    Ok(raw)
}

fn validate_manifest(m: &Manifest, offset: u64) -> Result<()> {
    ensure!(matches!(m.version, 1 | 2), "unsupported manifest version");
    let mut end = HEADER;
    for b in &m.blocks {
        ensure!(
            b.offset == end
                && b.raw_len > 0
                && b.raw_len as usize <= MAX_BLOCK
                && b.stored_len > 0
                && b.stored_len <= b.raw_len
                && b.codec <= if m.version == 1 { 1 } else { 4 },
            "invalid block bounds or codec"
        );
        ensure!(
            b.codec != 0 || b.stored_len == b.raw_len,
            "invalid stored block length"
        );
        end = end
            .checked_add(b.stored_len as u64)
            .context("block offset overflow")?;
        ensure!(end <= offset, "block outside payload");
    }
    ensure!(end == offset, "unaccounted payload data");
    let mut block_ends = vec![0u64; m.blocks.len()];
    for c in &m.chunks {
        let b = m
            .blocks
            .get(c.block as usize)
            .context("invalid chunk block reference")?;
        ensure!(
            c.len > 0 && c.offset as u64 == block_ends[c.block as usize],
            "invalid chunk layout"
        );
        let end = c.offset as u64 + c.len as u64;
        ensure!(end <= b.raw_len as u64, "chunk outside block");
        block_ends[c.block as usize] = end;
    }
    ensure!(
        m.blocks
            .iter()
            .zip(block_ends)
            .all(|(b, n)| b.raw_len as u64 == n),
        "unaccounted block data"
    );
    let mut names = HashSet::new();
    let dirs: HashSet<&str> = m.directories.iter().map(String::as_str).collect();
    for name in m.directories.iter().chain(m.files.iter().map(|f| &f.path)) {
        portable_path(name)?;
        ensure!(
            names.insert(name.to_lowercase()),
            "duplicate or conflicting archive path: {name}"
        );
        if let Some((parent, _)) = name.rsplit_once('/') {
            ensure!(dirs.contains(parent), "missing parent directory: {name}");
        }
    }
    let mut total = 0u64;
    let mut used = vec![false; m.chunks.len()];
    for f in &m.files {
        let mut len = 0u64;
        for &id in &f.chunks {
            let c = m
                .chunks
                .get(id as usize)
                .context("invalid file chunk reference")?;
            len = len
                .checked_add(c.len as u64)
                .context("file size overflow")?;
            used[id as usize] = true;
        }
        ensure!(
            len == f.size && f.modified_nanos < 1_000_000_000,
            "invalid file metadata"
        );
        total = total.checked_add(len).context("archive size overflow")?;
    }
    ensure!(used.into_iter().all(|used| used), "unreferenced chunks");
    Ok(())
}

struct Reader {
    file: File,
    manifest: Manifest,
    archive_bytes: u64,
    cache: VecDeque<(u32, Vec<u8>)>,
    cache_limit: usize,
    cache_bytes: usize,
    decoded: u64,
    verified_chunks: Vec<bool>,
}

impl Reader {
    fn open(path: &Path, cache_mib: usize) -> Result<Self> {
        ensure!(
            (64..=65536).contains(&cache_mib),
            "cache-mib must be 64..65536"
        );
        let mut file = File::open(path)?;
        let archive_bytes = file.metadata()?.len();
        let mut header = [0u8; HEADER as usize];
        file.read_exact(&mut header)
            .context("truncated archive header")?;
        ensure!(
            &header[..8] == MAGIC || &header[..8] == MAGIC_V1,
            "not a supported Cilo archive"
        );
        let offset = u64::from_le_bytes(header[8..16].try_into()?);
        let stored_len = u64::from_le_bytes(header[16..24].try_into()?);
        let raw_len = u64::from_le_bytes(header[24..32].try_into()?);
        ensure!(
            offset >= HEADER
                && stored_len > 0
                && stored_len <= MAX_MANIFEST as u64
                && raw_len <= MAX_MANIFEST as u64
                && raw_len > 0,
            "invalid manifest bounds"
        );
        ensure!(
            offset.checked_add(stored_len) == Some(archive_bytes),
            "truncated archive or trailing data"
        );
        file.seek(SeekFrom::Start(offset))?;
        let mut bytes = vec![0; stored_len as usize];
        file.read_exact(&mut bytes)?;
        let raw = decompress(&bytes, raw_len as usize).context("decoding manifest")?;
        ensure!(
            blake3::hash(&raw).as_bytes()[..] == header[32..64],
            "manifest checksum mismatch"
        );
        let manifest: Manifest = bincode::DefaultOptions::new()
            .with_fixint_encoding()
            .with_limit(MAX_MANIFEST as u64)
            .reject_trailing_bytes()
            .deserialize(&raw)
            .context("parsing manifest")?;
        validate_manifest(&manifest, offset)?;
        ensure!(
            manifest.version == header[4] as u32,
            "header/manifest version mismatch"
        );
        Ok(Self {
            file,
            verified_chunks: vec![false; manifest.chunks.len()],
            manifest,
            archive_bytes,
            cache: VecDeque::new(),
            cache_limit: cache_mib * MIB,
            cache_bytes: 0,
            decoded: 0,
        })
    }

    fn chunk(&mut self, id: u32) -> Result<&[u8]> {
        let c = &self.manifest.chunks[id as usize];
        let (block_id, start, len, hash) = (c.block, c.offset as usize, c.len as usize, c.hash);
        if let Some(index) = self.cache.iter().position(|(id, _)| *id == block_id) {
            let entry = self.cache.remove(index).context("cache index")?;
            self.cache.push_back(entry);
        } else {
            let b = &self.manifest.blocks[block_id as usize];
            // Evict before allocating a new decoded block.
            while self.cache_bytes + b.raw_len as usize > self.cache_limit {
                let (_, old) = self.cache.pop_front().context("cache capacity")?;
                self.cache_bytes -= old.len();
            }
            self.file.seek(SeekFrom::Start(b.offset))?;
            let mut bytes = vec![0; b.stored_len as usize];
            self.file.read_exact(&mut bytes)?;
            let mut raw = if b.codec == 0 {
                bytes
            } else {
                decompress(&bytes, b.raw_len as usize)?
            };
            if (2..=4).contains(&b.codec) {
                raw = filters::apply(&raw, b.codec, true);
            }
            ensure!(
                *blake3::hash(&raw).as_bytes() == b.hash,
                "block {block_id} checksum mismatch"
            );
            self.cache_bytes += raw.len();
            self.cache.push_back((block_id, raw));
            self.decoded += 1;
        }
        let raw = &self.cache.back().context("empty cache")?.1;
        let chunk = &raw[start..start + len];
        if !self.verified_chunks[id as usize] {
            ensure!(
                *blake3::hash(chunk).as_bytes() == hash,
                "chunk {id} checksum mismatch"
            );
            self.verified_chunks[id as usize] = true;
        }
        Ok(chunk)
    }

    fn process(&mut self, destination: Option<&Path>) -> Result<()> {
        for index in 0..self.manifest.files.len() {
            let entry = self.manifest.files[index].clone();
            let path = destination.map(|p| p.join(&entry.path));
            let mut output = path
                .as_ref()
                .map(|p| OpenOptions::new().write(true).create_new(true).open(p))
                .transpose()?;
            let mut hasher = blake3::Hasher::new();
            for id in entry.chunks {
                let chunk = self.chunk(id)?;
                hasher.update(chunk);
                if let Some(file) = &mut output {
                    file.write_all(chunk)?;
                }
            }
            ensure!(
                *hasher.finalize().as_bytes() == entry.hash,
                "file checksum mismatch: {}",
                entry.path
            );
            if let Some(file) = output {
                file.sync_all()?;
                drop(file);
                let path = path.context("missing destination")?;
                filetime::set_file_mtime(
                    &path,
                    filetime::FileTime::from_unix_time(
                        entry.modified_seconds,
                        entry.modified_nanos,
                    ),
                )?;
                #[cfg(unix)]
                if entry.executable {
                    use std::os::unix::fs::PermissionsExt;
                    fs::set_permissions(&path, fs::Permissions::from_mode(0o755))?;
                }
            }
        }
        Ok(())
    }
}

pub fn list(archive: &Path) -> Result<Manifest> {
    Ok(Reader::open(archive, 64)?.manifest)
}

#[derive(Serialize)]
pub struct FileInfo {
    pub path: String,
    pub size: u64,
    pub blake3: String,
    pub chunks: usize,
    pub modified_seconds: i64,
    pub modified_nanos: u32,
    pub executable: bool,
}

impl From<&Entry> for FileInfo {
    fn from(file: &Entry) -> Self {
        Self {
            path: file.path.clone(),
            size: file.size,
            blake3: blake3::Hash::from(file.hash).to_hex().to_string(),
            chunks: file.chunks.len(),
            modified_seconds: file.modified_seconds,
            modified_nanos: file.modified_nanos,
            executable: file.executable,
        }
    }
}

#[derive(Serialize)]
pub struct Contents {
    pub version: u32,
    pub archive_bytes: u64,
    pub input_bytes: u64,
    pub files: Vec<FileInfo>,
}

/// Inspect checked metadata without decoding payload blocks.
pub fn contents(archive: &Path) -> Result<Contents> {
    let reader = Reader::open(archive, 64)?;
    Ok(Contents {
        version: reader.manifest.version,
        archive_bytes: reader.archive_bytes,
        input_bytes: reader.manifest.files.iter().map(|f| f.size).sum(),
        files: reader.manifest.files.iter().map(FileInfo::from).collect(),
    })
}

#[derive(Serialize)]
pub struct Preview {
    pub file: FileInfo,
    pub bytes: Vec<u8>,
    pub truncated: bool,
    pub file_hash_verified: bool,
}

/// Read a bounded prefix directly from the archive. Unrelated blocks are untouched.
pub fn preview(archive: &Path, name: &str, max_bytes: usize) -> Result<Preview> {
    ensure!(
        (1..=4 * MIB).contains(&max_bytes),
        "max-bytes must be 1..4194304"
    );
    let mut reader = Reader::open(archive, 64)?;
    let entry = reader
        .manifest
        .files
        .iter()
        .find(|f| f.path == name)
        .context("file not found in archive")?
        .clone();
    let wanted = (max_bytes as u64).min(entry.size) as usize;
    let mut bytes = Vec::with_capacity(wanted);
    for &id in &entry.chunks {
        if bytes.len() == wanted {
            break;
        }
        let data = reader.chunk(id)?;
        let n = data.len().min(wanted - bytes.len());
        bytes.extend_from_slice(&data[..n]);
    }
    let complete = bytes.len() as u64 == entry.size;
    if complete {
        ensure!(
            *blake3::hash(&bytes).as_bytes() == entry.hash,
            "file checksum mismatch: {name}"
        );
    }
    Ok(Preview {
        file: FileInfo::from(&entry),
        bytes,
        truncated: !complete,
        file_hash_verified: complete,
    })
}

/// Stream one complete file to a writer, checking its full digest before returning.
pub fn read_file(archive: &Path, name: &str, output: &mut impl Write) -> Result<()> {
    let mut reader = Reader::open(archive, 64)?;
    let entry = reader
        .manifest
        .files
        .iter()
        .find(|f| f.path == name)
        .context("file not found in archive")?
        .clone();
    let mut hash = blake3::Hasher::new();
    for id in entry.chunks {
        let bytes = reader.chunk(id)?;
        hash.update(bytes);
        output.write_all(bytes)?;
    }
    ensure!(
        *hash.finalize().as_bytes() == entry.hash,
        "file checksum mismatch: {name}"
    );
    Ok(())
}

pub fn verify(archive: &Path, cache_mib: usize) -> Result<Stats> {
    let start = Instant::now();
    let mut reader = Reader::open(archive, cache_mib)?;
    reader.process(None)?;
    Ok(Stats::new(
        "verify",
        &reader.manifest,
        reader.archive_bytes,
        start,
        reader.decoded,
    ))
}

pub fn unpack(
    archive: &Path,
    destination: &Path,
    cache_mib: usize,
    max_output_gib: u64,
) -> Result<Stats> {
    let start = Instant::now();
    ensure!(
        fs::symlink_metadata(destination).is_err_and(|e| e.kind() == std::io::ErrorKind::NotFound),
        "destination already exists or is inaccessible"
    );
    let parent = destination
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let mut reader = Reader::open(archive, cache_mib)?;
    let total: u64 = reader.manifest.files.iter().map(|f| f.size).sum();
    if max_output_gib != 0 {
        let limit = max_output_gib
            .checked_mul(1024 * 1024 * 1024)
            .context("output limit overflow")?;
        ensure!(total <= limit, "archive exceeds max-output-gib");
    }
    let temp = tempfile::Builder::new()
        .prefix(".cilo-restore-")
        .tempdir_in(parent)?;
    for dir in &reader.manifest.directories {
        fs::create_dir_all(temp.path().join(dir))?;
    }
    reader.process(Some(temp.path()))?;
    ensure!(
        fs::symlink_metadata(destination).is_err_and(|e| e.kind() == std::io::ErrorKind::NotFound),
        "destination appeared during extraction"
    );
    fs::rename(temp.path(), destination).context("publishing restored directory")?;
    let _ = temp.keep();
    Ok(Stats::new(
        "unpack",
        &reader.manifest,
        reader.archive_bytes,
        start,
        reader.decoded,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reject_nonportable_paths() {
        for path in [
            "", "/x", "../x", "x/../y", "C:/x", "a\\b", "NUL", "com1.txt", "LPT²", "a/", "a//b",
            "a.", "a ", "a\0b",
        ] {
            assert!(portable_path(path).is_err(), "accepted {path:?}");
        }
        for path in ["build/data/file.xpak", "檔案.bin", "nulled.txt", "com10"] {
            portable_path(path).unwrap();
        }
    }
}
