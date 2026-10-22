// Read-only CASC sample acquisition helpers; the archiver itself is native Rust.
// Format references: wowdev.wiki/BLTE, wowdev.wiki/TACT, CascLib's TVFS reader.
import { createHash } from 'node:crypto';
import { inflateSync } from 'node:zlib';
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';

export const md5 = data => createHash('md5').update(data).digest('hex');
export const sha256 = data => createHash('sha256').update(data).digest('hex');
function requireThat(ok, message) { if (!ok) throw new Error(message); }

export function decodeBlte(data, expectedEkey, expectedCkey) {
  requireThat(data.subarray(0, 4).toString() === 'BLTE', 'not BLTE');
  const header = data.readUInt32BE(4);
  requireThat(header === 0 || (header >= 12 && header <= data.length), 'invalid BLTE header');
  if (expectedEkey) requireThat(md5(header ? data.subarray(0, header) : data) === expectedEkey, 'BLTE encoding-key mismatch');
  const decode = (chunk, limit) => {
    requireThat(chunk.length > 0, 'empty BLTE chunk');
    if (chunk[0] === 0x4e) return chunk.subarray(1);
    if (chunk[0] === 0x5a) return inflateSync(chunk.subarray(1), { maxOutputLength: limit });
    throw new Error(`unsupported BLTE mode ${String.fromCharCode(chunk[0])}; no decryption attempted`);
  };
  let raw;
  if (!header) raw = decode(data.subarray(8), 512 * 1024 * 1024);
  else {
    requireThat(data[8] === 0x0f, 'unknown BLTE table format');
    const count = data.readUIntBE(9, 3);
    requireThat(header === 12 + 24 * count, 'invalid BLTE chunk table');
    let offset = header, total = 0;
    const chunks = [];
    for (let i = 0; i < count; i++) {
      const at = 12 + 24 * i, size = data.readUInt32BE(at), rawSize = data.readUInt32BE(at + 4);
      total += rawSize;
      requireThat(total <= 512 * 1024 * 1024 && offset + size <= data.length, 'BLTE sample limit/bounds exceeded');
      const chunk = data.subarray(offset, offset + size);
      requireThat(md5(chunk) === data.subarray(at + 8, at + 24).toString('hex'), 'BLTE chunk checksum mismatch');
      const decoded = decode(chunk, rawSize || 1);
      requireThat(decoded.length === rawSize, 'BLTE decoded size mismatch');
      chunks.push(decoded); offset += size;
    }
    requireThat(offset === data.length, 'BLTE trailing data');
    raw = Buffer.concat(chunks);
  }
  if (expectedCkey) requireThat(md5(raw) === expectedCkey, 'BLTE content-key mismatch');
  return raw;
}

export function config(data) {
  return Object.fromEntries(data.toString().split(/\r?\n/).filter(l => l.includes(' = ')).map(l => {
    const at = l.indexOf(' = '); return [l.slice(0, at), l.slice(at + 3).trim().split(/\s+/)];
  }));
}

export function fetchKey(base, kind, key, cache) {
  requireThat(/^[a-f0-9]{32}$/.test(key), 'invalid CASC key');
  mkdirSync(cache, { recursive: true });
  const path = join(cache, `${kind}-${key}`);
  if (!existsSync(path)) {
    const url = `${base}/${kind}/${key.slice(0, 2)}/${key.slice(2, 4)}/${key}`;
    const result = execFileSync('curl', ['--fail', '--silent', '--show-error', '--location', '--retry', '3', '--max-time', '180', '--max-filesize', '536870912', url], { maxBuffer: 540 * 1024 * 1024 });
    if (kind === 'config') requireThat(md5(result) === key, 'configuration checksum mismatch');
    else decodeBlte(result, key);
    writeFileSync(path, result, { flag: 'wx' });
  }
  const data = readFileSync(path);
  if (kind === 'config') requireThat(md5(data) === key, 'configuration checksum mismatch');
  else decodeBlte(data, key);
  return data;
}

export function tvfs(data) {
  requireThat(data.subarray(0, 4).toString() === 'TVFS' && data[4] === 1, 'unsupported TVFS');
  const keySize = data[6], pathAt = data.readUInt32BE(12), pathSize = data.readUInt32BE(16);
  const vfsAt = data.readUInt32BE(20), vfsSize = data.readUInt32BE(24);
  const cftAt = data.readUInt32BE(28), cftSize = data.readUInt32BE(32);
  requireThat(pathAt + pathSize <= data.length && vfsAt + vfsSize <= data.length && cftAt + cftSize <= data.length, 'TVFS bounds');
  const width = cftSize > 0xffffff ? 4 : cftSize > 0xffff ? 3 : cftSize > 0xff ? 2 : 1;
  const files = [];
  const walk = (start, end, parent, depth) => {
    requireThat(depth < 128, 'TVFS nesting limit');
    let at = start, prefix = parent;
    while (at < end) {
      if (data[at] === 0) { prefix += '/'; at++; }
      if (data[at] !== 0xff) { const len = data[at++]; requireThat(at + len <= end, 'TVFS path bounds'); prefix += data.subarray(at, at + len).toString(); at += len; }
      if (data[at] === 0) { prefix += '/'; at++; }
      if (data[at] !== 0xff) { prefix += '/'; continue; }
      requireThat(at + 5 <= end, 'TVFS node bounds');
      const value = data.readUInt32BE(at + 1); at += 5;
      if (value & 0x80000000) {
        const stop = at + (value & 0x7fffffff) - 4;
        requireThat(stop >= at && stop <= end, 'TVFS directory bounds');
        walk(at, stop, prefix, depth + 1); at = stop;
      } else {
        let spanAt = vfsAt + value;
        requireThat(spanAt < vfsAt + vfsSize, 'TVFS VFS bounds');
        const count = data[spanAt++], spans = [];
        requireThat(count > 0 && spanAt + count * (8 + width) <= vfsAt + vfsSize, 'TVFS spans bounds');
        for (let i = 0; i < count; i++) {
          const offset = data.readUInt32BE(spanAt), size = data.readUInt32BE(spanAt + 4);
          const cft = cftAt + data.readUIntBE(spanAt + 8, width);
          requireThat(cft + keySize <= cftAt + cftSize, 'TVFS CFT bounds');
          spans.push({ offset, size, key: data.subarray(cft, cft + keySize).toString('hex') });
          spanAt += 8 + width;
        }
        files.push({ path: prefix.replace(/^\/+/, ''), spans });
      }
      prefix = parent;
    }
  };
  walk(pathAt, pathAt + pathSize, '', 0);
  return files;
}

export function downloadKeys(data) {
  requireThat(data.subarray(0, 2).toString() === 'DL' && data[2] === 1, 'sample collector supports DL v1 only');
  const keySize = data[3], checksums = data[4], count = data.readUInt32BE(5);
  requireThat(keySize === 16 && checksums <= 1, 'unsupported DL layout');
  const width = keySize + 5 + 1 + (checksums ? 4 : 0), map = new Map();
  requireThat(11 + count * width <= data.length, 'DL entries out of bounds');
  for (let i = 0; i < count; i++) {
    const at = 11 + i * width, key = data.subarray(at, at + keySize).toString('hex');
    const prefix = key.slice(0, 18);
    requireThat(!map.has(prefix), 'ambiguous DL encoding-key prefix');
    map.set(prefix, { key, stored: data.readUIntBE(at + keySize, 5) });
  }
  return map;
}
