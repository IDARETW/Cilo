use super::*;

pub(super) fn parse(input: &mut Input<'_>, size: u64) -> Result<Vec<Region>> {
    let header = read_at(input, 0, 408, size)?;
    ensure!(u16le(&header, 6) == 13, "unsupported XPAK version");
    ensure!(u64le(&header, 16) == size, "XPAK size mismatch");
    let data_at = u64le(&header, 320);
    let data_len = u64le(&header, 328);
    let count = u64le(&header, 336);
    let table_at = u64le(&header, 344);
    ensure!(
        data_at >= 408 && data_at <= table_at && data_len <= table_at - data_at,
        "invalid XPAK data bounds"
    );
    ensure!(
        count > 0 && count <= (MAX_REGIONS / 2) as u64,
        "invalid XPAK entry count"
    );
    ensure!(
        u64le(&header, 352) >= count * 24,
        "invalid XPAK hash table size"
    );
    let table = read_at(input, table_at, count as usize * 24, size)?;
    let mut spans = Vec::new();
    for entry in table.chunks_exact(24) {
        let offset = u64le(entry, 8);
        let len = u64le(entry, 16) & 0x00ff_ffff_ffff_ffff;
        ensure!(
            offset <= data_len && len <= data_len - offset,
            "XPAK object outside data region"
        );
        if len > 0 {
            spans.push((data_at + offset, data_at + offset + len, Kind::Generic));
        }
    }
    regions(spans, size)
}
