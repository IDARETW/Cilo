//! Byte-exact filters; applied after deduplication and reversed before checksums.
use crate::formats::Kind;
use anyhow::Result;

pub fn apply(raw: &[u8], codec: u8, inverse: bool) -> Vec<u8> {
    match codec {
        2 => {
            let mut data = raw.to_vec();
            let mut i = 0;
            while i + 5 <= data.len() {
                if matches!(data[i], 0xe8 | 0xe9) {
                    let value = u32::from_le_bytes(data[i + 1..i + 5].try_into().unwrap());
                    let pc = (i + 5) as u32;
                    let value = if inverse {
                        value.wrapping_sub(pc)
                    } else {
                        value.wrapping_add(pc)
                    };
                    data[i + 1..i + 5].copy_from_slice(&value.to_le_bytes());
                    i += 5;
                } else {
                    i += 1;
                }
            }
            data
        }
        3 | 4 => {
            let width = if codec == 3 { 2 } else { 4 };
            let count = raw.len() / width;
            let mut data = vec![0; raw.len()];
            for lane in 0..width {
                for i in 0..count {
                    if inverse {
                        data[i * width + lane] = raw[lane * count + i];
                    } else {
                        data[lane * count + i] = raw[i * width + lane];
                    }
                }
            }
            data[count * width..].copy_from_slice(&raw[count * width..]);
            data
        }
        _ => raw.to_vec(),
    }
}

/// Sample several locations to avoid expensive full-block trials on packed code
/// or on numeric-looking sections that do not benefit from byte shuffling.
pub fn candidate(raw: &[u8], kind: Kind) -> Result<Option<u8>> {
    let codecs: &[u8] = match kind {
        Kind::Code => &[2],
        Kind::Numeric => &[3, 4],
        _ => return Ok(None),
    };
    if raw.len() < 4096 {
        return Ok(None);
    }
    let n = raw.len().min(64 * 1024);
    let mut totals = vec![0usize; codecs.len() + 1];
    let mut compressor = zstd::bulk::Compressor::new(3)?;
    for pos in [0, (raw.len() - n) / 2, raw.len() - n] {
        let sample = &raw[pos..pos + n];
        totals[0] += compressor.compress(sample)?.len();
        for (i, codec) in codecs.iter().enumerate() {
            totals[i + 1] += compressor.compress(&apply(sample, *codec, false))?.len();
        }
    }
    let (index, best) = totals.iter().enumerate().min_by_key(|(_, n)| *n).unwrap();
    Ok((index > 0 && best * 100 < totals[0] * 99).then(|| codecs[index - 1]))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn filters_are_invertible_at_all_tails_and_wrapping_addresses() {
        let mut data = Vec::new();
        for i in 0u32..20000 {
            data.push(if i % 2 == 0 { 0xe8 } else { 0xe9 });
            data.extend_from_slice(&i.wrapping_mul(0xfab3e9e8).to_le_bytes());
        }
        for codec in 2..=4 {
            for len in (0..32).chain((data.len() - 16)..=data.len()) {
                assert_eq!(
                    apply(&apply(&data[..len], codec, false), codec, true),
                    data[..len]
                );
            }
        }
    }
}
