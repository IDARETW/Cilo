import { test } from 'node:test';
import assert from 'node:assert/strict';
import { deflateSync } from 'node:zlib';
import { decodeBlte, md5 } from './casc.mjs';

function fixture(raw, mode) {
  const data = mode === 'Z' ? deflateSync(raw) : raw;
  const chunk = Buffer.concat([Buffer.from(mode), data]);
  const header = Buffer.alloc(36);
  header.write('BLTE'); header.writeUInt32BE(36, 4); header[8] = 15; header.writeUIntBE(1, 9, 3);
  header.writeUInt32BE(chunk.length, 12); header.writeUInt32BE(raw.length, 16);
  Buffer.from(md5(chunk), 'hex').copy(header, 20);
  return { encoded: Buffer.concat([header, chunk]), ekey: md5(header), ckey: md5(raw) };
}
for (const mode of ['N', 'Z']) test(`BLTE ${mode} round trip and integrity`, () => {
  const raw = Buffer.from('game content\n'.repeat(500));
  const f = fixture(raw, mode);
  assert.deepEqual(decodeBlte(f.encoded, f.ekey, f.ckey), raw);
  f.encoded[f.encoded.length - 1] ^= 1;
  assert.throws(() => decodeBlte(f.encoded, f.ekey, f.ckey), /checksum mismatch/);
});
test('BLTE rejects wrong keys, unsupported encryption and truncated data', () => {
  const f = fixture(Buffer.from('data'), 'E');
  assert.throws(() => decodeBlte(f.encoded, '0'.repeat(32)), /encoding-key mismatch/);
  assert.throws(() => decodeBlte(f.encoded, f.ekey), /unsupported BLTE mode E/);
  assert.throws(() => decodeBlte(f.encoded.subarray(0, 36), f.ekey), /bounds/);
});
