use super::*;

pub(super) fn parse(input: &mut Input<'_>, size: u64) -> Result<Vec<Region>> {
    let header = read_at(input, 0, 56, size)?;
    ensure!(
        u32le(&header, 4) == 10 && u32le(&header, 8) == 44,
        "unsupported SAB version/entry size"
    );
    ensure!(u64le(&header, 32) == size, "SAB size mismatch");
    let count = u32le(&header, 20) as usize;
    ensure!(
        count > 0 && count <= MAX_REGIONS / 4,
        "invalid SAB entry count"
    );
    let table_at = u64le(&header, 40);
    let table = read_at(input, table_at, count * 44, size)?;
    let mut spans = Vec::new();
    for entry in table.chunks_exact(44) {
        let start = u64le(entry, 20);
        let seek_len = u32le(entry, 8) as u64;
        let len = u32le(entry, 4) as u64;
        ensure!(
            start >= 56 && start <= table_at && seek_len + len <= table_at - start,
            "SAB audio outside data region"
        );
        if seek_len > 0 {
            spans.push((start, start + seek_len, Kind::Generic));
        }
        if len > 0 {
            spans.push((start + seek_len, start + seek_len + len, Kind::Generic));
        }
    }
    regions(spans, size)
}
