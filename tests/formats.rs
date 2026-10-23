use cilo::{PackOptions, Profile, formats, list, pack, unpack, verify};
use std::fs;

fn put32(data: &mut [u8], at: usize, value: u32) {
    data[at..at + 4].copy_from_slice(&value.to_le_bytes());
}
fn put64(data: &mut [u8], at: usize, value: u64) {
    data[at..at + 8].copy_from_slice(&value.to_le_bytes());
}

fn pe() -> Vec<u8> {
    let size = 512 * 1024;
    let mut data = vec![0x90; size + 512];
    data[..512].fill(0);
    data[..2].copy_from_slice(b"MZ");
    put32(&mut data, 60, 128);
    data[128..132].copy_from_slice(b"PE\0\0");
    data[132..134].copy_from_slice(&0x8664u16.to_le_bytes());
    data[134..136].copy_from_slice(&1u16.to_le_bytes());
    data[148..150].copy_from_slice(&240u16.to_le_bytes());
    data[152..154].copy_from_slice(&0x20bu16.to_le_bytes());
    put32(&mut data, 212, 512);
    let section = 392;
    data[section..section + 5].copy_from_slice(b".text");
    put32(&mut data, section + 16, size as u32);
    put32(&mut data, section + 20, 512);
    put32(&mut data, section + 36, 0x60000020);
    for pos in (512..data.len() - 5).step_by(8) {
        data[pos] = 0xe8;
        put32(
            &mut data,
            pos + 1,
            0x12345678u32.wrapping_sub(pos as u32 + 5),
        );
    }
    data
}

fn sound(reverse: bool) -> Vec<u8> {
    let count = 192;
    let audio_len = 32768;
    let table = 4096 + count * audio_len;
    let size = table + count * 44;
    let mut data = vec![0; size];
    put32(&mut data, 0, 0x23585532);
    put32(&mut data, 4, 10);
    put32(&mut data, 8, 44);
    put32(&mut data, 20, count as u32);
    put64(&mut data, 32, size as u64);
    put64(&mut data, 40, table as u64);
    for i in 0..count {
        let id = if reverse { count - i } else { i + 1 };
        let start = 4096 + i * audio_len;
        let mut state = id as u64 * 0x123456789abcdef;
        for b in &mut data[start..start + audio_len] {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            *b = state as u8;
        }
        put32(&mut data, table + i * 44, id as u32);
        put32(&mut data, table + i * 44 + 4, audio_len as u32);
        put64(&mut data, table + i * 44 + 20, start as u64);
    }
    data
}

fn fastfile(patch: bool) -> Vec<u8> {
    let header_len = if patch { 328 } else { 136 };
    let start = header_len + 32768;
    let mut data = vec![0; start + 12 + 12 + 128];
    data[..8].copy_from_slice(if patch { b"IWffd100" } else { b"IWffa100" });
    put32(&mut data, 8, if patch { 6 } else { 11 });
    if patch {
        data[56..64].copy_from_slice(b"IWffa100");
        data[192..200].copy_from_slice(b"IWffa100");
    }
    put32(&mut data, start, 0x43574902);
    put32(&mut data, start + 4, 65536);
    put32(&mut data, start + 12, 128);
    put32(&mut data, start + 16, 65536);
    data[start + 24..].fill(42);
    data
}

fn bink() -> Vec<u8> {
    let mut data = vec![0; 512];
    data[..4].copy_from_slice(b"KB2j");
    put32(&mut data, 4, 504);
    put32(&mut data, 8, 2);
    put32(&mut data, 48, 65); // low bit is a keyframe flag
    put32(&mut data, 52, 256);
    put32(&mut data, 56, 512);
    data
}

fn xpak() -> Vec<u8> {
    let mut data = vec![0; 32768 + 1024 + 24];
    let size = data.len();
    data[..4].copy_from_slice(b"KAPI");
    data[6..8].copy_from_slice(&13u16.to_le_bytes());
    put64(&mut data, 16, size as u64);
    put64(&mut data, 320, 32768);
    put64(&mut data, 328, 1024);
    put64(&mut data, 336, 1);
    put64(&mut data, 344, 32768 + 1024);
    put64(&mut data, 352, 24);
    put64(&mut data, 32768 + 1024 + 16, 0x8000000000000400);
    data
}

#[test]
fn recognized_layouts_and_invalid_variants_preserve_every_byte() {
    let temp = tempfile::tempdir().unwrap();
    let input = temp.path().join("input");
    fs::create_dir(&input).unwrap();
    for (name, data) in [
        ("exe", pe()),
        ("sabs", sound(false)),
        ("ff", fastfile(false)),
        ("fp", fastfile(true)),
        ("bik", bink()),
        ("xpak", xpak()),
    ] {
        // Content recognition must work even with unrelated filename extensions.
        let path = input.join(format!("{name}.unknown"));
        fs::write(&path, &data).unwrap();
        let plan = formats::inspect(&path).unwrap();
        assert!(
            plan.fallback_reason.is_none(),
            "{name}: {:?}",
            plan.fallback_reason
        );
        assert!(plan.regions.len() > 1);
        assert_eq!(plan.regions.last().unwrap().end, data.len() as u64);
        for len in [0, 1, 8, 16, 48, 64, 128] {
            let short = input.join(format!("{name}-{len}"));
            fs::write(&short, &data[..len]).unwrap();
            formats::inspect(&short).unwrap(); // malformed inputs must safely fall back
        }
        let bad = input.join(format!("{name}-bad"));
        let mut corrupted = data.clone();
        let at = match name {
            "exe" => 60,
            "sabs" => 20,
            "bik" => 48,
            "xpak" => 344,
            _ => 8,
        };
        corrupted[at..at + 4].fill(255);
        fs::write(&bad, corrupted).unwrap();
        assert!(formats::inspect(&bad).unwrap().fallback_reason.is_some());
    }
    let archive = temp.path().join("all.cilo");
    pack(
        &input,
        &archive,
        PackOptions {
            profile: Profile::Fast,
            threads: 2,
            block_mib: 4,
            ..PackOptions::default()
        },
    )
    .unwrap();
    assert!(list(&archive).unwrap().blocks.iter().any(|b| b.codec == 2));
    verify(&archive, 64).unwrap();
    let restored = temp.path().join("out");
    unpack(&archive, &restored, 64, 0).unwrap();
    for entry in fs::read_dir(&input).unwrap() {
        let p = entry.unwrap();
        assert_eq!(
            fs::read(p.path()).unwrap(),
            fs::read(restored.join(p.file_name())).unwrap()
        );
    }
}

#[test]
fn reordered_audio_assets_deduplicate_at_container_boundaries() {
    let temp = tempfile::tempdir().unwrap();
    let input = temp.path().join("input");
    fs::create_dir(&input).unwrap();
    fs::write(input.join("bank-a.sabs"), sound(false)).unwrap();
    fs::write(input.join("bank-b.sabs"), sound(true)).unwrap();
    let options = PackOptions {
        profile: Profile::Fast,
        threads: 1,
        block_mib: 4,
        ..PackOptions::default()
    };
    let aware = pack(&input, &temp.path().join("aware.cilo"), options).unwrap();
    let generic = pack(
        &input,
        &temp.path().join("generic.cilo"),
        PackOptions {
            format_aware: false,
            ..options
        },
    )
    .unwrap();
    assert!(aware.unique_bytes * 10 < aware.input_bytes * 6);
    assert!(aware.unique_bytes * 10 < generic.unique_bytes * 6);
    assert!(aware.archive_bytes < generic.archive_bytes);
    verify(&temp.path().join("aware.cilo"), 64).unwrap();
}

#[test]
fn restores_original_v1_archive() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("legacy.cilo");
    fs::write(&path, include_bytes!("fixtures/v1.archive")).unwrap();
    assert_eq!(list(&path).unwrap().version, 1);
    verify(&path, 64).unwrap();
    let out = temp.path().join("restored");
    unpack(&path, &out, 64, 0).unwrap();
    assert_eq!(
        fs::read(out.join("hello.txt")).unwrap(),
        b"Cilo v1 compatibility fixture\n"
    );
}

#[test]
fn fastfile_blocks_cross_authentication_pages_without_losing_bytes() {
    let temp = tempfile::tempdir().unwrap();
    let mut data = fastfile(false);
    let start = 136 + 32768;
    data.truncate(start + 12);
    put32(&mut data, start + 4, 65536 * 3);
    let mut logical = Vec::new();
    for length in [0x400000u32, 0x400000, 128] {
        logical.extend_from_slice(&length.to_le_bytes());
        logical.extend_from_slice(&65536u32.to_le_bytes());
        logical.extend_from_slice(&0u32.to_le_bytes());
        logical.resize(logical.len() + length as usize, 37);
    }
    let first = 0x800000 - 12;
    data.extend_from_slice(&logical[..first]);
    let page = data.len();
    data.resize(data.len() + 16384, 0xa7);
    data.extend_from_slice(&logical[first..]);
    let input = temp.path().join("input");
    fs::create_dir(&input).unwrap();
    let path = input.join("cross.ff");
    fs::write(&path, &data).unwrap();
    let plan = formats::inspect(&path).unwrap();
    assert!(plan.fallback_reason.is_none());
    assert!(plan.regions.iter().any(|r| r.end == page as u64));
    assert!(plan.regions.iter().any(|r| r.end == (page + 16384) as u64));
    let archive = temp.path().join("cross.cilo");
    pack(
        &input,
        &archive,
        PackOptions {
            profile: Profile::Fast,
            ..PackOptions::default()
        },
    )
    .unwrap();
    let out = temp.path().join("out");
    unpack(&archive, &out, 64, 0).unwrap();
    assert_eq!(fs::read(out.join("cross.ff")).unwrap(), data);
}

#[test]
fn bink_legacy_and_audio_tables_need_no_terminal_index_entry() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("movie.bik");
    for (magic, extra) in [(b"BIKi", 0), (b"BIKk", 4), (b"KB2j", 4)] {
        let mut data = vec![0; 513];
        data[..4].copy_from_slice(magic);
        put32(&mut data, 4, 505);
        put32(&mut data, 8, 2);
        put32(&mut data, 40, 1); // one audio track: three four-byte table entries
        let table = 44 + extra + 12;
        let first = table + 8;
        put32(&mut data, table, first as u32 | 1);
        put32(&mut data, table + 4, 256);
        data[first..].fill(0x77); // payload follows immediately; no terminal offset
        fs::write(&path, data).unwrap();
        let plan = formats::inspect(&path).unwrap();
        assert!(
            plan.fallback_reason.is_none(),
            "{magic:?}: {:?}",
            plan.fallback_reason
        );
        assert_eq!(plan.regions.last().unwrap().end, 513);
    }
}
