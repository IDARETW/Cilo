use cilo::{PackOptions, Profile, list, pack, unpack, verify};
use std::{
    fs,
    io::{Read, Seek, SeekFrom, Write},
    path::Path,
};

fn options() -> PackOptions {
    PackOptions {
        profile: Profile::Fast,
        threads: 2,
        block_mib: 4,
        chunk_kib: 64,
        dedup: true,
        format_aware: true,
    }
}

fn random_bytes(n: usize) -> Vec<u8> {
    let mut state = 0x123456789abcdef0u64;
    (0..n)
        .map(|_| {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            state as u8
        })
        .collect()
}

fn same_tree(a: &Path, b: &Path) {
    for entry in walkdir::WalkDir::new(a).min_depth(1) {
        let entry = entry.unwrap();
        let other = b.join(entry.path().strip_prefix(a).unwrap());
        if entry.file_type().is_dir() {
            assert!(other.is_dir());
        } else {
            assert_eq!(fs::read(entry.path()).unwrap(), fs::read(other).unwrap());
        }
    }
}

#[test]
fn multiblock_shifted_duplicates_roundtrip_and_metadata() {
    let temp = tempfile::tempdir().unwrap();
    let input = temp.path().join("input");
    fs::create_dir_all(input.join("v1/empty")).unwrap();
    fs::create_dir_all(input.join("v2")).unwrap();
    let data = random_bytes(10 * 1024 * 1024);
    fs::write(input.join("v1/data.xpak"), &data).unwrap();
    let mut shifted = b"a small insertion shifts all fixed block boundaries".to_vec();
    shifted.extend_from_slice(&data);
    fs::write(input.join("v2/data.xpak"), &shifted).unwrap();
    fs::write(input.join("v2/copy.xpak"), &data).unwrap();
    fs::write(input.join("檔案.txt"), b"hello world\n").unwrap();
    fs::write(input.join("empty"), b"").unwrap();
    let timestamp = filetime::FileTime::from_unix_time(1_600_000_000, 123_456_700);
    filetime::set_file_mtime(input.join("檔案.txt"), timestamp).unwrap();
    let archive = temp.path().join("data.cilo");
    let result = pack(&input, &archive, options()).unwrap();
    assert_eq!(result.files, 5);
    assert!(result.unique_bytes < result.input_bytes / 2);
    assert!(list(&archive).unwrap().blocks.len() >= 3);
    verify(&archive, 64).unwrap();
    let restored = temp.path().join("restored");
    unpack(&archive, &restored, 64, 1).unwrap();
    same_tree(&input, &restored);
    let restored_time = filetime::FileTime::from_last_modification_time(
        &fs::metadata(restored.join("檔案.txt")).unwrap(),
    );
    assert_eq!(restored_time, timestamp);
    assert!(unpack(&archive, &restored, 64, 0).is_err());
    assert!(pack(&input, &archive, options()).is_err());
}

#[test]
fn empty_directory_and_empty_files() {
    let temp = tempfile::tempdir().unwrap();
    let input = temp.path().join("input");
    fs::create_dir(&input).unwrap();
    for n in 0..2 {
        let archive = temp.path().join(format!("{n}.cilo"));
        pack(&input, &archive, options()).unwrap();
        verify(&archive, 64).unwrap();
        let restored = temp.path().join(format!("out-{n}"));
        unpack(&archive, &restored, 64, 0).unwrap();
        same_tree(&input, &restored);
        fs::write(input.join("empty-file"), b"").unwrap();
    }
}

#[test]
fn corruption_truncation_and_atomic_restore() {
    let temp = tempfile::tempdir().unwrap();
    let input = temp.path().join("input");
    fs::create_dir(&input).unwrap();
    fs::write(input.join("data"), random_bytes(300_000)).unwrap();
    let archive = temp.path().join("good.cilo");
    pack(&input, &archive, options()).unwrap();
    let bytes = fs::read(&archive).unwrap();
    for (name, pos) in [
        ("header", 0),
        ("payload", 70),
        ("manifest", bytes.len() - 10),
    ] {
        let mut corrupt = bytes.clone();
        corrupt[pos] ^= 0x80;
        let path = temp.path().join(format!("{name}.cilo"));
        fs::write(&path, corrupt).unwrap();
        assert!(verify(&path, 64).is_err());
        let output = temp.path().join(name);
        assert!(unpack(&path, &output, 64, 0).is_err());
        assert!(!output.exists());
    }
    for len in [0, 8, 63, bytes.len() / 2, bytes.len() - 1] {
        let path = temp.path().join("short.cilo");
        fs::write(&path, &bytes[..len]).unwrap();
        assert!(verify(&path, 64).is_err());
    }
    let mut extra = bytes;
    extra.push(0);
    fs::write(temp.path().join("extra.cilo"), extra).unwrap();
    assert!(verify(&temp.path().join("extra.cilo"), 64).is_err());
}

// Rewrite a checksum-valid manifest to test structural validation, not just checksums.
fn tamper_manifest(path: &Path, edit: impl FnOnce(&mut serde_json::Value)) {
    let mut file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(path)
        .unwrap();
    let mut header = [0u8; 64];
    file.read_exact(&mut header).unwrap();
    let offset = u64::from_le_bytes(header[8..16].try_into().unwrap());
    let raw_len = u64::from_le_bytes(header[24..32].try_into().unwrap());
    file.seek(SeekFrom::Start(offset)).unwrap();
    let mut encoded = Vec::new();
    file.read_to_end(&mut encoded).unwrap();
    let raw = zstd::bulk::decompress(&encoded, raw_len as usize).unwrap();
    let manifest: cilo::Manifest = bincode::deserialize(&raw).unwrap();
    let mut value = serde_json::to_value(manifest).unwrap();
    edit(&mut value);
    let manifest: cilo::Manifest = serde_json::from_value(value).unwrap();
    let raw = bincode::serialize(&manifest).unwrap();
    let encoded = zstd::bulk::compress(&raw, 3).unwrap();
    header[16..24].copy_from_slice(&(encoded.len() as u64).to_le_bytes());
    header[24..32].copy_from_slice(&(raw.len() as u64).to_le_bytes());
    header[32..64].copy_from_slice(blake3::hash(&raw).as_bytes());
    file.seek(SeekFrom::Start(0)).unwrap();
    file.write_all(&header).unwrap();
    file.seek(SeekFrom::Start(offset)).unwrap();
    file.write_all(&encoded).unwrap();
    file.set_len(offset + encoded.len() as u64).unwrap();
}

#[test]
fn rejects_malicious_paths_and_references() {
    let temp = tempfile::tempdir().unwrap();
    let input = temp.path().join("input");
    fs::create_dir(&input).unwrap();
    fs::write(input.join("data"), b"important bytes").unwrap();
    let original = temp.path().join("original.cilo");
    pack(&input, &original, options()).unwrap();
    for name in [
        "../outside",
        "/outside",
        "C:/outside",
        "a\\b",
        "NUL",
        "a/../../b",
    ] {
        let archive = temp.path().join("evil.cilo");
        fs::copy(&original, &archive).unwrap();
        tamper_manifest(&archive, |v| v["files"][0]["path"] = name.into());
        assert!(list(&archive).is_err());
        assert!(unpack(&archive, &temp.path().join("out"), 64, 0).is_err());
    }
    for field in ["block", "offset", "len"] {
        let archive = temp.path().join("evil.cilo");
        fs::copy(&original, &archive).unwrap();
        tamper_manifest(&archive, |v| v["chunks"][0][field] = u32::MAX.into());
        assert!(verify(&archive, 64).is_err());
    }
    for (field, value) in [("codec", 255), ("raw_len", u32::MAX)] {
        let archive = temp.path().join("evil.cilo");
        fs::copy(&original, &archive).unwrap();
        tamper_manifest(&archive, |v| v["blocks"][0][field] = value.into());
        assert!(verify(&archive, 64).is_err());
    }
}

#[test]
fn bounded_compression_pipeline_is_deterministic_and_handles_hash_misses() {
    let temp = tempfile::tempdir().unwrap();
    let input = temp.path().join("input");
    fs::create_dir(&input).unwrap();
    let mut data = random_bytes(36 * 1024 * 1024);
    fs::write(input.join("a.bin"), &data).unwrap();
    data[13] ^= 0x80;
    fs::write(input.join("b.bin"), &data).unwrap(); // same length, different digest
    fs::write(input.join("c.bin"), &data).unwrap(); // whole-file reuse
    let one = temp.path().join("one.cilo");
    let four = temp.path().join("four.cilo");
    pack(
        &input,
        &one,
        PackOptions {
            threads: 1,
            ..options()
        },
    )
    .unwrap();
    pack(
        &input,
        &four,
        PackOptions {
            threads: 4,
            ..options()
        },
    )
    .unwrap();
    assert_eq!(fs::read(&one).unwrap(), fs::read(&four).unwrap());
    verify(&four, 64).unwrap();
    let out = temp.path().join("out");
    unpack(&four, &out, 64, 0).unwrap();
    same_tree(&input, &out);
}

#[test]
fn profiles_and_no_dedup_work() {
    let temp = tempfile::tempdir().unwrap();
    let input = temp.path().join("input");
    fs::create_dir(&input).unwrap();
    fs::write(input.join("a"), vec![42; 500_000]).unwrap();
    fs::copy(input.join("a"), input.join("b")).unwrap();
    for (i, profile) in [Profile::Fast, Profile::Balanced, Profile::Max]
        .into_iter()
        .enumerate()
    {
        let archive = temp.path().join(format!("{i}.cilo"));
        let stats = pack(
            &input,
            &archive,
            PackOptions {
                profile,
                dedup: false,
                ..options()
            },
        )
        .unwrap();
        assert_eq!(stats.unique_bytes, stats.input_bytes);
        assert!(stats.archive_bytes < stats.input_bytes / 10);
        verify(&archive, 64).unwrap();
    }
}

#[cfg(unix)]
#[test]
fn rejects_symlinks_case_collisions_and_preserves_executable() {
    use std::os::unix::fs::{PermissionsExt, symlink};
    let temp = tempfile::tempdir().unwrap();
    let input = temp.path().join("input");
    fs::create_dir(&input).unwrap();
    fs::write(input.join("file"), b"#!/bin/sh\nexit 0\n").unwrap();
    fs::set_permissions(input.join("file"), fs::Permissions::from_mode(0o755)).unwrap();
    symlink("file", input.join("link")).unwrap();
    let archive = temp.path().join("archive.cilo");
    assert!(pack(&input, &archive, options()).is_err());
    fs::remove_file(input.join("link")).unwrap();
    fs::write(input.join("FILE"), b"collision").unwrap();
    assert!(pack(&input, &archive, options()).is_err());
    fs::remove_file(input.join("FILE")).unwrap();
    pack(&input, &archive, options()).unwrap();
    let output = temp.path().join("out");
    unpack(&archive, &output, 64, 0).unwrap();
    assert_ne!(
        fs::metadata(output.join("file"))
            .unwrap()
            .permissions()
            .mode()
            & 0o111,
        0
    );
    assert!(pack(&input, &input.join("self.cilo"), options()).is_err());
}
