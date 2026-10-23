use anyhow::Result;
use clap::{Parser, Subcommand};
use std::path::PathBuf;

#[derive(Parser)]
#[command(
    version,
    about = "Lossless archives with cross-file/build deduplication"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Archive a directory. Put multiple builds below this directory for deduplication.
    Pack {
        source: PathBuf,
        archive: PathBuf,
        #[arg(long, value_enum, default_value = "balanced")]
        profile: cilo::Profile,
        /// Parallel solid compression jobs (each may use substantial memory in max mode).
        #[arg(long, default_value_t = cilo::default_threads())]
        threads: usize,
        #[arg(long, default_value_t = 64)]
        block_mib: usize,
        /// Average content-defined chunk size, power of two, 16..1024 KiB.
        #[arg(long, default_value_t = 256)]
        chunk_kib: u32,
        /// Disable deduplication for comparative benchmarks.
        #[arg(long)]
        no_dedup: bool,
        /// Disable container boundaries and reversible filters for comparison.
        #[arg(long)]
        generic: bool,
    },
    /// Restore into a NEW directory, verifying every restored file.
    Unpack {
        archive: PathBuf,
        destination: PathBuf,
        #[arg(long, default_value_t = 256)]
        cache_mib: usize,
        /// Refuse archives exceeding this declared restored size (0 means no limit).
        #[arg(long, default_value_t = 0)]
        max_output_gib: u64,
    },
    /// Restore selected members from a JSON array of paths into a NEW directory.
    UnpackSelected {
        archive: PathBuf,
        destination: PathBuf,
        selection: PathBuf,
        #[arg(long, default_value_t = 256)]
        cache_mib: usize,
        #[arg(long, default_value_t = 0)]
        max_output_gib: u64,
    },
    /// Read and verify all blocks, chunks and files without extracting.
    Verify {
        archive: PathBuf,
        #[arg(long, default_value_t = 256)]
        cache_mib: usize,
    },
    /// Print checksum-verified archive metadata; does not verify payload blocks.
    List { archive: PathBuf },
    /// Inspect validated container boundaries for one file without modifying it.
    Analyze { file: PathBuf },
    /// Download integration: archive a CILOSTR1 stdin stream, staging one file at a time.
    PackStream {
        archive: PathBuf,
        #[arg(long, value_enum, default_value = "balanced")]
        profile: cilo::Profile,
        #[arg(long, default_value_t = cilo::default_threads())]
        threads: usize,
        #[arg(long, default_value_t = 16)]
        max_file_gib: u64,
        /// Abort before publication if this cooperative cancellation file exists.
        #[arg(long)]
        cancel_file: Option<PathBuf>,
    },
    /// List file details without decoding payload blocks.
    Contents { archive: PathBuf },
    /// Preview a file as bounded JSON bytes without extracting it.
    Preview {
        archive: PathBuf,
        path: String,
        #[arg(long, default_value_t = 65536)]
        max_bytes: usize,
    },
    /// Stream one complete verified file to stdout without extracting the archive.
    Cat { archive: PathBuf, path: String },
}

fn run() -> Result<()> {
    let stats = match Cli::parse().command {
        Command::Pack {
            source,
            archive,
            profile,
            threads,
            block_mib,
            chunk_kib,
            no_dedup,
            generic,
        } => cilo::pack(
            &source,
            &archive,
            cilo::PackOptions {
                profile,
                threads,
                block_mib,
                chunk_kib,
                dedup: !no_dedup,
                format_aware: !generic,
            },
        )?,
        Command::Unpack {
            archive,
            destination,
            cache_mib,
            max_output_gib,
        } => cilo::unpack(&archive, &destination, cache_mib, max_output_gib)?,
        Command::UnpackSelected {
            archive,
            destination,
            selection,
            cache_mib,
            max_output_gib,
        } => {
            anyhow::ensure!(
                std::fs::metadata(&selection)?.len() <= 8 * 1024 * 1024,
                "selection JSON exceeds 8 MiB"
            );
            let names: Vec<String> = serde_json::from_reader(std::fs::File::open(selection)?)?;
            cilo::unpack_selected(&archive, &destination, &names, cache_mib, max_output_gib)?
        }
        Command::Verify { archive, cache_mib } => cilo::verify(&archive, cache_mib)?,
        Command::List { archive } => {
            println!("{}", serde_json::to_string_pretty(&cilo::list(&archive)?)?);
            return Ok(());
        }
        Command::Analyze { file } => {
            println!(
                "{}",
                serde_json::to_string_pretty(&cilo::formats::inspect(&file)?)?
            );
            return Ok(());
        }
        Command::PackStream {
            archive,
            profile,
            threads,
            max_file_gib,
            cancel_file,
        } => cilo::stream::pack(
            std::io::stdin().lock(),
            &archive,
            cilo::PackOptions {
                profile,
                threads,
                ..Default::default()
            },
            max_file_gib,
            cancel_file.as_deref(),
        )?,
        Command::Contents { archive } => {
            println!("{}", serde_json::to_string(&cilo::contents(&archive)?)?);
            return Ok(());
        }
        Command::Preview {
            archive,
            path,
            max_bytes,
        } => {
            println!(
                "{}",
                serde_json::to_string(&cilo::preview(&archive, &path, max_bytes)?)?
            );
            return Ok(());
        }
        Command::Cat { archive, path } => {
            cilo::read_file(&archive, &path, &mut std::io::stdout().lock())?;
            return Ok(());
        }
    };
    println!("{}", serde_json::to_string_pretty(&stats)?);
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("cilo: {error:#}");
        std::process::exit(1);
    }
}
