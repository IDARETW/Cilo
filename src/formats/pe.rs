use super::*;

pub(super) fn parse(input: &mut Input<'_>, size: u64) -> Result<Vec<Region>> {
    let dos = read_at(input, 0, 64, size)?;
    let pe = u32le(&dos, 60) as u64;
    let header = read_at(input, pe, 24, size)?;
    ensure!(&header[..4] == b"PE\0\0", "invalid PE signature");
    ensure!(
        matches!(u16le(&header, 4), 0x14c | 0x8664),
        "unsupported PE machine"
    );
    let count = u16le(&header, 6) as usize;
    ensure!((1..=96).contains(&count), "invalid PE section count");
    let optional_size = u16le(&header, 20) as usize;
    let optional = read_at(input, pe + 24, optional_size, size)?;
    ensure!(
        optional.len() >= 64 && matches!(u16le(&optional, 0), 0x10b | 0x20b),
        "invalid PE optional header"
    );
    let table_at = pe + 24 + optional_size as u64;
    let table = read_at(input, table_at, count * 40, size)?;
    let headers_end = u32le(&optional, 60) as u64;
    ensure!(
        headers_end >= table_at + table.len() as u64 && headers_end <= size,
        "invalid PE header size"
    );
    let mut spans = Vec::new();
    for section in table.chunks_exact(40) {
        let len = u32le(section, 16) as u64;
        if len == 0 {
            continue;
        }
        let start = u32le(section, 20) as u64;
        ensure!(start >= headers_end, "PE section overlaps headers");
        let flags = u32le(section, 36);
        let kind = if flags & 0x20000000 != 0 {
            Kind::Code
        } else if section.starts_with(b".data\0") || section.starts_with(b".pdata\0") {
            Kind::Numeric
        } else {
            Kind::Generic
        };
        spans.push((start, start + len, kind));
    }
    regions(spans, size)
}
