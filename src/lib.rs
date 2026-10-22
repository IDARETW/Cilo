//! Cilo v1: content-defined deduplication followed by independent solid Zstandard blocks.
use anyhow::{Context, Result, bail, ensure};
use bincode::Options;
use clap::ValueEnum;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::{
    collections::{HashMap, HashSet, VecDeque},
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    time::Instant,
};

const MAGIC: &[u8; 8] = b"CILO\x01\r\n\x1a";
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
}

impl Default for PackOptions {
    fn default() -> Self {
        Self {
            profile: Profile::Balanced,
            threads: default_threads(),
            block_mib: 32,
            chunk_kib: 256,
            dedup: true,
        }
    }
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Block {
    pub offset: u64,
    pub stored_len: u32,
    pub raw_len: u32,
    /// 0 = stored verbatim, 1 = Zstandard frame.
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
    pending: Vec<Vec<u8>>,
    current: Vec<u8>,
    options: PackOptions,
    pool: rayon::ThreadPool,
}

impl<'a> BlockWriter<'a> {
    fn new(file: &'a mut File, options: PackOptions) -> Result<Self> {
        Ok(Self {
            file,
            blocks: Vec::new(),
            pending: Vec::new(),
            current: Vec::new(),
            options,
            pool: rayon::ThreadPoolBuilder::new()
                .num_threads(options.threads)
                .build()?,
        })
    }

    fn flush_pending(&mut self) -> Result<()> {
        let level = self.options.profile.level();
        let results: Vec<Result<EncodedBlock>> = self.pool.install(|| {
            self.pending
                .par_iter()
                .map(|raw| {
                    let mut compressor = zstd::bulk::Compressor::new(level)?;
                    use zstd::zstd_safe::CParameter;
                    // Bound the match window, including max mode, to the largest supported block.
                    compressor.set_parameter(CParameter::WindowLog(26))?;
                    compressor.set_parameter(CParameter::ChecksumFlag(true))?;
                    compressor.set_parameter(CParameter::EnableLongDistanceMatching(true))?;
                    let compressed = compressor.compress(raw)?;
                    let hash = *blake3::hash(raw).as_bytes();
                    let len = raw.len() as u32;
                    if compressed.len() < raw.len() {
                        Ok((compressed, 1, hash, len))
                    } else {
                        Ok((raw.clone(), 0, hash, len))
                    }
                })
                .collect()
        });
        for result in results {
            let (bytes, codec, hash, raw_len) = result?;
            let offset = self.file.stream_position()?;
            self.file.write_all(&bytes)?;
            self.blocks.push(Block {
                offset,
                stored_len: bytes.len() as u32,
                raw_len,
                codec,
                hash,
            });
        }
        self.pending.clear();
        Ok(())
    }

    fn finish_current(&mut self) -> Result<()> {
        if !self.current.is_empty() {
            self.pending.push(std::mem::take(&mut self.current));
        }
        if self.pending.len() >= self.options.threads {
            self.flush_pending()?;
        }
        Ok(())
    }

    fn push(&mut self, data: &[u8], hash: Digest) -> Result<Chunk> {
        if self.current.len() + data.len() > self.options.block_mib * MIB {
            self.finish_current()?;
        }
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

/// Pack a directory without changing source files or replacing an existing archive.
pub fn pack(source: &Path, archive: &Path, options: PackOptions) -> Result<Stats> {
    let start = Instant::now();
    ensure!((1..=64).contains(&options.threads), "threads must be 1..64");
    ensure!(
        (4..=64).contains(&options.block_mib),
        "block-mib must be 4..64"
    );
    ensure!(
        (64..=1024).contains(&options.chunk_kib) && options.chunk_kib.is_power_of_two(),
        "chunk-kib must be a power of two in 64..1024"
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
        version: 1,
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
    let mut temp = tempfile::NamedTempFile::new_in(&parent)?;
    temp.write_all(&[0; HEADER as usize])?;
    let mut writer = BlockWriter::new(temp.as_file_mut(), options)?;
    let mut seen: HashMap<Digest, u32> = HashMap::new();
    let avg = options.chunk_kib * 1024;
    for (name, path) in sources {
        ensure!(
            fs::symlink_metadata(&path)?.is_file(),
            "source changed type: {name}"
        );
        let file = File::open(&path).with_context(|| format!("opening {name}"))?;
        let before = file.metadata()?;
        let modified = filetime::FileTime::from_last_modification_time(&before);
        let mut hash = blake3::Hasher::new();
        let mut chunks = Vec::new();
        let mut size = 0u64;
        let mut stream = fastcdc::v2020::StreamCDC::new(file, avg / 4, avg, avg * 4);
        for piece in &mut stream {
            let piece = piece.with_context(|| format!("reading {name}"))?;
            let data = piece.data;
            hash.update(&data);
            size = size
                .checked_add(data.len() as u64)
                .context("file size overflow")?;
            let digest = *blake3::hash(&data).as_bytes();
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
                    manifest.chunks.push(writer.push(&data, digest)?);
                    if options.dedup {
                        seen.insert(digest, id);
                    }
                    id
                }
            };
            chunks.push(id);
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
        manifest.files.push(Entry {
            path: name,
            size,
            hash: *hash.finalize().as_bytes(),
            chunks,
            modified_seconds: modified.unix_seconds(),
            modified_nanos: modified.nanoseconds(),
            executable,
        });
    }
    manifest.blocks = writer.finish()?;
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
    ensure!(m.version == 1, "unsupported manifest version");
    let mut end = HEADER;
    for b in &m.blocks {
        ensure!(
            b.offset == end
                && b.raw_len > 0
                && b.raw_len as usize <= MAX_BLOCK
                && b.stored_len > 0
                && b.stored_len <= b.raw_len
                && b.codec <= 1,
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
        ensure!(&header[..8] == MAGIC, "not a supported Cilo archive");
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
        Ok(Self {
            file,
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
            let raw = if b.codec == 0 {
                bytes
            } else {
                decompress(&bytes, b.raw_len as usize)?
            };
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
        ensure!(
            *blake3::hash(chunk).as_bytes() == hash,
            "chunk {id} checksum mismatch"
        );
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
