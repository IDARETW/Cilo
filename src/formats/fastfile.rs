use super::*;

// Secure IW streams interleave 16 KiB hash pages between 8 MiB data windows.
// Hashes, padding and Oodle bytes stay untouched; only physical chunk boundaries change.
pub(super) fn parse(input: &mut Input<'_>, size: u64, patch: bool) -> Result<Vec<Region>> {
    let header = read_at(input, 0, if patch { 328 } else { 136 }, size)?;
    ensure!(
        u32le(&header, 8) == if patch { 6 } else { 11 },
        "unsupported fastfile version"
    );
    if patch {
        ensure!(
            &header[56..64] == b"IWffa100" && &header[192..200] == b"IWffa100",
            "invalid patch headers"
        );
    }
    let start = header.len() as u64 + 32768;
    let secure = read_at(input, start, 12, size)?;
    ensure!(
        u32le(&secure, 0) == 0x43574902,
        "unsupported fastfile stream wrapper"
    );
    let expected = u32le(&secure, 4) as u64;
    ensure!(expected > 0, "empty fastfile stream");
    let mut pos = start + 12;
    let mut window_end = start + 0x800000;
    let mut decoded = 0u64;
    let mut cuts = vec![pos];
    while decoded < expected {
        ensure!(cuts.len() < MAX_REGIONS - 4, "too many fastfile blocks");
        // A block header itself can cross an authentication-page boundary.
        let mut block = [0u8; 12];
        let mut filled = 0;
        while filled < block.len() {
            advance_page(&mut pos, &mut window_end, &mut cuts, size)?;
            let len = (window_end - pos).min((block.len() - filled) as u64) as usize;
            block[filled..filled + len].copy_from_slice(&read_at(input, pos, len, size)?);
            pos += len as u64;
            filled += len;
        }
        let stored = u32le(&block, 0) as u64;
        let raw = u32le(&block, 4) as u64;
        ensure!(
            stored > 0
                && stored <= 0x400000
                && raw > 0
                && raw <= 0x400000
                && raw <= expected - decoded,
            "invalid Oodle block sizes"
        );
        ensure!(u32le(&block, 8) == 0, "unsupported Oodle block flags");
        let mut remaining = (stored + 3) & !3;
        while remaining > 0 {
            advance_page(&mut pos, &mut window_end, &mut cuts, size)?;
            let len = remaining.min(window_end - pos);
            ensure!(pos <= size && len <= size - pos, "truncated Oodle block");
            pos += len;
            remaining -= len;
        }
        decoded += raw;
        cuts.push(pos);
    }
    ensure!(size - pos <= 32768, "unexpected fastfile trailer");
    if pos < size {
        cuts.push(size);
    }
    cuts.sort_unstable();
    cuts.dedup();
    Ok(cuts
        .into_iter()
        .map(|end| Region {
            end,
            kind: Kind::Generic,
        })
        .collect())
}

fn advance_page(pos: &mut u64, end: &mut u64, cuts: &mut Vec<u64>, size: u64) -> Result<()> {
    if *pos == *end {
        cuts.push(*pos);
        ensure!(
            *pos <= size && 16384 <= size - *pos,
            "truncated authentication page"
        );
        *pos += 16384;
        cuts.push(*pos);
        *end = *pos + 0x800000;
    }
    Ok(())
}
