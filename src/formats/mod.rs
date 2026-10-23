//! Conservative, read-only container parsers. Layout hints never change source bytes.
//! Unknown versions and invalid layouts fall back to ordinary content-defined chunks.
mod bink;
mod fastfile;
mod pe;
mod sound;
mod xpak;

use anyhow::{Result, ensure};
use serde::Serialize;
use std::{
    fs::File,
    io::{BufReader, Read, Seek, SeekFrom},
};

const MAX_REGIONS: usize = 1_000_000;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
pub enum Kind {
    #[default]
    Generic,
    Code,
    Numeric,
}

#[derive(Debug, Serialize)]
pub struct Region {
    pub end: u64,
    pub kind: Kind,
}

#[derive(Debug, Serialize)]
pub struct Plan {
    pub format: &'static str,
    pub regions: Vec<Region>,
    pub fallback_reason: Option<String>,
}

type Input<'a> = BufReader<&'a mut File>;

fn read_at(input: &mut Input<'_>, offset: u64, len: usize, size: u64) -> Result<Vec<u8>> {
    ensure!(
        offset <= size && len as u64 <= size - offset,
        "table outside file"
    );
    input.seek(SeekFrom::Start(offset))?;
    let mut bytes = vec![0; len];
    input.read_exact(&mut bytes)?;
    Ok(bytes)
}

fn u16le(bytes: &[u8], at: usize) -> u16 {
    u16::from_le_bytes(bytes[at..at + 2].try_into().unwrap())
}
fn u32le(bytes: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(bytes[at..at + 4].try_into().unwrap())
}
fn u64le(bytes: &[u8], at: usize) -> u64 {
    u64::from_le_bytes(bytes[at..at + 8].try_into().unwrap())
}

fn regions(mut spans: Vec<(u64, u64, Kind)>, size: u64) -> Result<Vec<Region>> {
    ensure!(spans.len() <= MAX_REGIONS / 2, "too many container entries");
    spans.sort_unstable_by_key(|s| (s.0, s.1));
    spans.dedup();
    let mut result = Vec::new();
    let mut end = 0;
    for (start, stop, kind) in spans {
        ensure!(
            start >= end && start < stop && stop <= size,
            "overlapping or invalid container entries"
        );
        if start > end {
            result.push(Region {
                end: start,
                kind: Kind::Generic,
            });
        }
        result.push(Region { end: stop, kind });
        end = stop;
    }
    if end < size {
        result.push(Region {
            end: size,
            kind: Kind::Generic,
        });
    }
    Ok(result)
}

pub fn plan(file: &mut File, size: u64, enabled: bool) -> Result<Plan> {
    let mut input = BufReader::with_capacity(64 * 1024, file);
    let header = read_at(&mut input, 0, size.min(16) as usize, size)?;
    let (format, parsed) = if !enabled {
        ("generic", Ok(regions(Vec::new(), size)?))
    } else if header.starts_with(b"MZ") {
        ("PE", pe::parse(&mut input, size))
    } else if header.starts_with(b"IWffa100") || header.starts_with(b"IWffd100") {
        (
            "IW fastfile",
            fastfile::parse(&mut input, size, header.starts_with(b"IWffd100")),
        )
    } else if header.starts_with(&0x23585532u32.to_le_bytes()) {
        ("SAB sound bank", sound::parse(&mut input, size))
    } else if header.starts_with(b"KB2") || header.starts_with(b"BIK") {
        ("Bink video", bink::parse(&mut input, size))
    } else if header.starts_with(b"KAPI") {
        ("XPAK", xpak::parse(&mut input, size))
    } else {
        ("generic", regions(Vec::new(), size))
    };
    let plan = match parsed {
        Ok(regions) => Plan {
            format,
            regions,
            fallback_reason: None,
        },
        Err(error) => Plan {
            format,
            regions: regions(Vec::new(), size)?,
            fallback_reason: Some(error.to_string()),
        },
    };
    input.seek(SeekFrom::Start(0))?;
    Ok(plan)
}

/// Describe a single file's validated layout without decompressing or executing it.
pub fn inspect(path: &std::path::Path) -> Result<Plan> {
    let mut file = File::open(path)?;
    let size = file.metadata()?.len();
    plan(&mut file, size, true)
}
