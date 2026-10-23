//! Framed stdin ingestion for downloaders. Only one decoded file is staged at a time.
use crate::*;

pub const MAGIC: &[u8; 8] = b"CILOSTR1";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FileHeader {
    path: String,
    size: u64,
    #[serde(default)]
    modified_seconds: i64,
    #[serde(default)]
    modified_nanos: u32,
}

struct Sources<'a, R> {
    input: R,
    scratch: &'a Path,
    cancel: Option<&'a Path>,
    current: Option<tempfile::NamedTempFile>,
    max_file_bytes: u64,
    finished: bool,
}

impl<R: Read> Sources<'_, R> {
    fn next_file(&mut self) -> Result<Option<(String, PathBuf)>> {
        self.current = None;
        check_cancelled(self.cancel)?;
        let mut length = [0; 4];
        self.input
            .read_exact(&mut length)
            .context("missing stream terminator or file header")?;
        let length = u32::from_le_bytes(length) as usize;
        if length == 0 {
            let mut extra = [0];
            ensure!(
                self.input.read(&mut extra)? == 0,
                "data after stream terminator"
            );
            return Ok(None);
        }
        ensure!(length <= 65536, "stream file header exceeds 64 KiB");
        let mut metadata = vec![0; length];
        self.input.read_exact(&mut metadata)?;
        let header: FileHeader =
            serde_json::from_slice(&metadata).context("invalid stream file header")?;
        portable_path(&header.path)?;
        ensure!(
            header.size <= self.max_file_bytes,
            "stream file exceeds max-file-gib"
        );
        ensure!(
            header.modified_nanos < 1_000_000_000,
            "invalid stream timestamp"
        );
        let mut file = tempfile::NamedTempFile::new_in(self.scratch)?;
        let mut buffer = vec![0; 256 * 1024];
        let mut remaining = header.size;
        while remaining > 0 {
            check_cancelled(self.cancel)?;
            let n = remaining.min(buffer.len() as u64) as usize;
            self.input
                .read_exact(&mut buffer[..n])
                .context("truncated stream file")?;
            file.write_all(&buffer[..n])?;
            remaining -= n as u64;
        }
        filetime::set_file_mtime(
            file.path(),
            filetime::FileTime::from_unix_time(header.modified_seconds, header.modified_nanos),
        )?;
        let path = file.path().to_path_buf();
        self.current = Some(file);
        Ok(Some((header.path, path)))
    }
}

impl<R: Read> Iterator for Sources<'_, R> {
    type Item = Result<(String, PathBuf)>;
    fn next(&mut self) -> Option<Self::Item> {
        if self.finished {
            return None;
        }
        match self.next_file() {
            Ok(Some(file)) => Some(Ok(file)),
            Ok(None) => {
                self.finished = true;
                None
            }
            Err(error) => {
                self.finished = true;
                Some(Err(error))
            }
        }
    }
}

pub fn pack<R: Read>(
    mut input: R,
    archive: &Path,
    options: PackOptions,
    max_file_gib: u64,
    cancel: Option<&Path>,
) -> Result<Stats> {
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
        (1..=1024).contains(&max_file_gib),
        "max-file-gib must be 1..1024"
    );
    let parent = archive
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."))
        .canonicalize()?;
    let destination = parent.join(archive.file_name().context("missing archive filename")?);
    ensure!(
        fs::symlink_metadata(&destination).is_err_and(|e| e.kind() == std::io::ErrorKind::NotFound),
        "archive destination already exists or is inaccessible"
    );
    let mut magic = [0; 8];
    input.read_exact(&mut magic)?;
    ensure!(&magic == MAGIC, "unsupported input stream");
    let scratch = tempfile::Builder::new()
        .prefix(".cilo-stream-")
        .tempdir_in(&parent)?;
    let sources = Sources {
        input,
        scratch: scratch.path(),
        cancel,
        current: None,
        max_file_bytes: max_file_gib * 1024 * 1024 * 1024,
        finished: false,
    };
    let manifest = Manifest {
        version: 2,
        directories: Vec::new(),
        files: Vec::new(),
        chunks: Vec::new(),
        blocks: Vec::new(),
    };
    let mut stats = pack_sources(
        sources,
        manifest,
        parent,
        destination,
        options,
        start,
        cancel,
    )?;
    stats.operation = "pack-stream".into();
    Ok(stats)
}
