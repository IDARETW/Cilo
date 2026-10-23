use super::*;

pub(super) fn parse(input: &mut Input<'_>, size: u64) -> Result<Vec<Region>> {
    let header = read_at(input, 0, 48, size)?;
    ensure!(
        matches!(
            &header[..4],
            b"KB2j" | b"KB2k" | b"KB2i" | b"BIKb" | b"BIKf" | b"BIKg" | b"BIKh" | b"BIKi" | b"BIKk"
        ),
        "unsupported Bink revision"
    );
    ensure!(u32le(&header, 4) as u64 + 8 == size, "Bink size mismatch");
    let frames = u32le(&header, 8) as usize;
    let tracks = u32le(&header, 40) as u64;
    ensure!(
        frames > 0 && frames <= MAX_REGIONS / 2 && tracks <= 256,
        "invalid Bink counts"
    );
    // Bink 2 i/j/k and Bink 1 k add a four-byte field before audio tables.
    let extra = if header.starts_with(b"KB2") || &header[..4] == b"BIKk" {
        4
    } else {
        0
    };
    let table_at = 44 + extra + tracks * 12;
    let table = read_at(input, table_at, frames * 4, size)?;
    let mut spans = Vec::new();
    let mut start = (u32le(&table, 0) & !1) as u64;
    ensure!(
        start >= table_at + table.len() as u64,
        "Bink frame overlaps index"
    );
    for i in 1..=frames {
        let end = if i == frames {
            size
        } else {
            (u32le(&table, i * 4) & !1) as u64
        };
        ensure!(end > start && end <= size, "invalid Bink frame offsets");
        spans.push((start, end, Kind::Generic));
        start = end;
    }
    ensure!(start == size, "Bink terminal offset mismatch");
    regions(spans, size)
}
