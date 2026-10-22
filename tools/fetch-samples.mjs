// Usage: node tools/fetch-samples.mjs /absolute/path/to/sample-workspace
// Downloads a fixed, small subset of three MW2019 builds; never executes game files.
import { mkdirSync, writeFileSync, existsSync, readFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { config, fetchKey, decodeBlte, tvfs, downloadKeys, sha256 } from './casc.mjs';

const output = resolve(process.argv[2] || 'samples');
const base = 'http://us.cdn.blizzard.com/tpr/odin';
const builds = [
  { id: '11521872', config: 'd88aae6a2bcd3944b48a6b80b5b46c26' },
  { id: '11531527', config: 'c5dcafb637755b8f7398230f6e677303' },
  { id: '11744825', config: '8429ca4513e894bc28232b61ad1321fd' },
];
const selected = [
  'common.ff', 'captive_exterior_tr.ff',
  'pak_base_vol138.xpak', 'pak_base_vol140.xpak',
  'ai_chatter_wz.sabs', 'br_event_x2_sfx.sabs',
  'nvngx_dlss.dll', 'campaignmenu.bik',
  // Include assets/patch files whose encoding keys change across these builds.
  'code_post_gfx.fp', 'common_base_mp.fp', 'cp_arms_dealer_00001_tr.ff',
];
const records = [];
for (const build of builds) {
  console.log(`Reading build ${build.id}`);
  const cfg = config(fetchKey(base, 'config', build.config, join(output, 'cache')));
  const manifest = name => {
    const [ckey, ekey] = cfg[name];
    return decodeBlte(fetchKey(base, 'data', ekey, join(output, 'cache')), ekey, ckey);
  };
  const files = tvfs(manifest('vfs-root'));
  const keys = downloadKeys(manifest('download'));
  const dir = join(output, 'corpus', build.id);
  mkdirSync(dir, { recursive: true });
  for (const name of selected) {
    const entry = files.find(f => f.path === name);
    if (!entry || entry.spans.length !== 1 || entry.spans[0].offset !== 0) throw new Error(`missing/unsupported sample ${build.id}/${name}`);
    const span = entry.spans[0], encoded = keys.get(span.key);
    if (!encoded || span.size > 128 * 1024 * 1024) throw new Error(`missing/oversized sample ${name}`);
    console.log(`  ${name}: ${(span.size / 1048576).toFixed(2)} MiB`);
    const blte = fetchKey(base, 'data', encoded.key, join(output, 'cache'));
    if (blte.length !== encoded.stored) throw new Error(`encoded size mismatch ${name}`);
    const raw = decodeBlte(blte, encoded.key);
    if (raw.length !== span.size) throw new Error(`content size mismatch ${name}`);
    const path = join(dir, name);
    if (existsSync(path)) {
      if (sha256(readFileSync(path)) !== sha256(raw)) throw new Error(`existing sample differs: ${path}`);
    } else writeFileSync(path, raw, { flag: 'wx' });
    records.push({ build: build.id, build_config: build.config, path: name, bytes: raw.length,
      sha256: sha256(raw), encoding_key: encoded.key, encoded_bytes: blte.length,
      url: `${base}/data/${encoded.key.slice(0, 2)}/${encoded.key.slice(2, 4)}/${encoded.key}` });
  }
}
const report = { source: 'Every-US-CASC-Build-ngdp_data.txt, snapshot d8bcfafa67e7408c95d33caa3733729aff17c6fe',
  description: 'Complete selected game files after BLTE transport decoding. Native game asset compression is preserved. No payload is committed.', records };
writeFileSync(join(output, 'corpus.json'), JSON.stringify(report, null, 2) + '\n');
console.log(`Verified ${records.length} files, ${records.reduce((n, r) => n + r.bytes, 0)} bytes. Manifest: ${join(output, 'corpus.json')}`);
