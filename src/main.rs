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
        #[arg(long, default_value_t = 32)]
        block_mib: usize,
        /// Average content-defined chunk size, power of two, 64..1024 KiB.
        #[arg(long, default_value_t = 256)]
        chunk_kib: u32,
        /// Disable deduplication for comparative benchmarks.
        #[arg(long)]
        no_dedup: bool,
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
    /// Read and verify all blocks, chunks and files without extracting.
    Verify {
        archive: PathBuf,
        #[arg(long, default_value_t = 256)]
        cache_mib: usize,
    },
    /// Print checksum-verified archive metadata; does not verify payload blocks.
    List { archive: PathBuf },
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
        } => cilo::pack(
            &source,
            &archive,
            cilo::PackOptions {
                profile,
                threads,
                block_mib,
                chunk_kib,
                dedup: !no_dedup,
            },
        )?,
        Command::Unpack {
            archive,
            destination,
            cache_mib,
            max_output_gib,
        } => cilo::unpack(&archive, &destination, cache_mib, max_output_gib)?,
        Command::Verify { archive, cache_mib } => cilo::verify(&archive, cache_mib)?,
        Command::List { archive } => {
            println!("{}", serde_json::to_string_pretty(&cilo::list(&archive)?)?);
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
