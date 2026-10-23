use cilo::{PackOptions, Profile, contents, pack, preview, read_file, stream, unpack, verify};
use std::{fs, io::Cursor};

fn append(data: &mut Vec<u8>, name: &str, bytes: &[u8]) {
    let json = serde_json::to_vec(&serde_json::json!({"path": name, "size": bytes.len()})).unwrap();
    data.extend_from_slice(&(json.len() as u32).to_le_bytes());
    data.extend(json);
    data.extend_from_slice(bytes);
}

fn options() -> PackOptions {
    PackOptions {
        profile: Profile::Fast,
        threads: 2,
        block_mib: 4,
        ..Default::default()
    }
}

#[test]
fn framed_download_restores_nested_empty_and_duplicate_files_and_cleans_scratch() {
    let temp = tempfile::tempdir().unwrap();
    let archive = temp.path().join("download.cilo");
    let mut input = stream::MAGIC.to_vec();
    append(&mut input, "build/one.txt", b"original contents\n");
    append(&mut input, "second/two.txt", b"original contents\n");
    append(&mut input, "second/empty", b"");
    input.extend([0; 4]);
    let stats = stream::pack(Cursor::new(input), &archive, options(), 1, None).unwrap();
    assert_eq!(stats.files, 3);
    assert_eq!(stats.operation, "pack-stream");
    assert_eq!(fs::read_dir(temp.path()).unwrap().count(), 1);
    verify(&archive, 64).unwrap();
    let out = temp.path().join("restored");
    unpack(&archive, &out, 64, 0).unwrap();
    assert_eq!(
        fs::read(out.join("second/two.txt")).unwrap(),
        b"original contents\n"
    );
    assert!(fs::read(out.join("second/empty")).unwrap().is_empty());
}

#[test]
fn interrupted_invalid_and_cancelled_streams_never_publish_or_leave_staging() {
    let temp = tempfile::tempdir().unwrap();
    let archive = temp.path().join("download.cilo");
    let mut good = stream::MAGIC.to_vec();
    append(&mut good, "file", &[7; 5000]);
    good.extend([0; 4]);
    for len in [0, 7, 8, 10, 32, good.len() - 1] {
        assert!(stream::pack(Cursor::new(&good[..len]), &archive, options(), 1, None).is_err());
        assert_eq!(fs::read_dir(temp.path()).unwrap().count(), 0);
    }
    for names in [
        ["../escape", "ok"],
        ["a", "a"],
        ["A/a", "a/b"],
        ["x", "x/y"],
    ] {
        let mut input = stream::MAGIC.to_vec();
        for name in names {
            append(&mut input, name, b"data");
        }
        input.extend([0; 4]);
        assert!(stream::pack(Cursor::new(input), &archive, options(), 1, None).is_err());
        assert_eq!(fs::read_dir(temp.path()).unwrap().count(), 0);
    }
    let mut extra = good.clone();
    extra.push(1);
    assert!(stream::pack(Cursor::new(extra), &archive, options(), 1, None).is_err());
    let flag = temp.path().join("cancel");
    fs::write(&flag, b"").unwrap();
    assert!(stream::pack(Cursor::new(&good), &archive, options(), 1, Some(&flag)).is_err());
    assert_eq!(fs::read_dir(temp.path()).unwrap().count(), 1);
    fs::remove_file(flag).unwrap();
    let mut oversized = stream::MAGIC.to_vec();
    let header = br#"{"path":"huge","size":1073741825}"#;
    oversized.extend_from_slice(&(header.len() as u32).to_le_bytes());
    oversized.extend(header);
    assert!(stream::pack(Cursor::new(oversized), &archive, options(), 1, None).is_err());
    fs::write(&archive, b"existing data").unwrap();
    assert!(stream::pack(Cursor::new(&good), &archive, options(), 1, None).is_err());
    assert_eq!(fs::read(&archive).unwrap(), b"existing data");
}

#[test]
fn preview_and_full_file_reads_are_bounded_and_do_not_touch_unrelated_blocks() {
    let temp = tempfile::tempdir().unwrap();
    let input = temp.path().join("input");
    fs::create_dir(&input).unwrap();
    let mut state = 0x123456789abcdef0u64;
    let data: Vec<u8> = (0..10 * 1024 * 1024)
        .map(|_| {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            state as u8
        })
        .collect();
    fs::write(input.join("large.bin"), data).unwrap();
    fs::write(input.join("hello.txt"), b"hello world\n").unwrap();
    let archive = temp.path().join("test.cilo");
    pack(&input, &archive, options()).unwrap();
    assert_eq!(contents(&archive).unwrap().files.len(), 2);
    let short = preview(&archive, "hello.txt", 5).unwrap();
    assert_eq!(short.bytes, b"hello");
    assert!(short.truncated);
    assert!(!short.file_hash_verified);
    let full = preview(&archive, "hello.txt", 100).unwrap();
    assert!(full.file_hash_verified);
    assert!(!full.truncated);
    assert!(preview(&archive, "missing", 100).is_err());
    assert!(preview(&archive, "hello.txt", usize::MAX).is_err());
    let mut bytes = fs::read(&archive).unwrap();
    bytes[70] ^= 128;
    fs::write(&archive, bytes).unwrap();
    assert!(verify(&archive, 64).is_err());
    assert!(
        preview(&archive, "hello.txt", 100)
            .unwrap()
            .file_hash_verified
    );
    let mut output = Vec::new();
    read_file(&archive, "hello.txt", &mut output).unwrap();
    assert_eq!(output, b"hello world\n");
    assert_eq!(fs::read_dir(temp.path()).unwrap().count(), 2); // no extracted preview files
}
