// Linux benchmark harness; requires Node 20+, GNU tar, zstd and xz.
// Usage: node tools/benchmark.mjs SAMPLE_WORKSPACE RESULT.json [REPEATS=3]
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync, mkdtempSync, rmSync, statSync, readdirSync } from 'node:fs';
import { resolve, join, dirname } from 'node:path';
import { performance } from 'node:perf_hooks';
import { cpus, platform, release, totalmem } from 'node:os';
import { sha256 } from './casc.mjs';

const workspace = resolve(process.argv[2]);
const resultFile = resolve(process.argv[3]);
const repeats = Number(process.argv[4] || 3);
if (!Number.isInteger(repeats) || repeats < 1 || repeats > 10) throw new Error('repeats must be 1..10');
const executable = resolve('target/release/cilo');
const corpus = JSON.parse(readFileSync(join(workspace, 'corpus.json')));
const run = (cmd, args) => execFileSync(cmd, args, { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'] });
function checkTree(root, expected) {
  const files = readdirSync(root, { recursive: true, withFileTypes: true }).filter(e => e.isFile());
  if (files.length !== expected.length) throw new Error('restored file count differs');
  for (const r of expected) if (sha256(readFileSync(join(root, r.relative))) !== r.sha256) throw new Error(`round-trip mismatch: ${r.relative}`);
}
const datasets = [
  { name: 'single-build', path: join(workspace, 'corpus', '11521872'), records: corpus.records.filter(r => r.build === '11521872').map(r => ({ ...r, relative: r.path })) },
  { name: 'three-builds', path: join(workspace, 'corpus'), records: corpus.records.map(r => ({ ...r, relative: `${r.build}/${r.path}` })) },
];
const results = { measured_at_utc: new Date().toISOString(), platform: platform(), kernel: release(), cpu: cpus()[0].model,
  logical_cpus: cpus().length, memory_bytes: totalmem(), threads: 4, repeats, rust: run('rustc', ['--version']).trim(),
  zstd: run('zstd', ['--version']).trim(), xz: run('xz', ['--version']).trim(),
  notes: 'Warm source cache; sequential runs on a shared cloud host. Cilo timers include checksums and file I/O/sync. Baselines include tar creation/extraction but not an fsync guarantee. Independent SHA-256 validation is outside timers. No network time included.', rows: [] };
mkdirSync(dirname(resultFile), { recursive: true });
const temp = mkdtempSync(join(workspace, 'bench-'));
try {
  for (const dataset of datasets) {
    checkTree(dataset.path, dataset.records);
    const bytes = dataset.records.reduce((n, r) => n + r.bytes, 0);
    for (const mode of ['fast', 'balanced', 'max', 'tar-zstd-19', 'tar-xz-6']) {
      for (let iteration = 1; iteration <= repeats; iteration++) {
        console.log(`${dataset.name} ${mode} run ${iteration}/${repeats}`);
        const archive = join(temp, 'archive'), output = join(temp, 'out'), tar = join(temp, 'input.tar');
        let row;
        if (!mode.startsWith('tar-')) {
          const pack = JSON.parse(run(executable, ['pack', dataset.path, archive, '--profile', mode, '--threads', '4']));
          const unpack = JSON.parse(run(executable, ['unpack', archive, output]));
          row = { dataset: dataset.name, mode, iteration, bytes, archive_bytes: pack.archive_bytes, unique_bytes: pack.unique_bytes,
            compression_seconds: pack.elapsed_seconds, decompression_seconds: unpack.elapsed_seconds,
            compression_mib_s: pack.throughput_mib_s, decompression_mib_s: unpack.throughput_mib_s };
        } else {
          const start = performance.now();
          run('tar', ['--sort=name', '--mtime=@0', '--owner=0', '--group=0', '--numeric-owner', '-cf', tar, '-C', dataset.path, '.']);
          if (mode === 'tar-zstd-19') run('zstd', ['-q', '-T4', '-19', '--long=26', tar, '-o', archive]);
          else run('xz', ['-T4', '-6', '--keep', tar]);
          const archivePath = mode === 'tar-zstd-19' ? archive : `${tar}.xz`;
          const compression = (performance.now() - start) / 1000;
          rmSync(tar); mkdirSync(output);
          const decodeStart = performance.now();
          if (mode === 'tar-zstd-19') run('zstd', ['-q', '-d', archivePath, '-o', tar]);
          else run('xz', ['--decompress', '--keep', archivePath]);
          run('tar', ['-xf', tar, '-C', output]);
          const decompression = (performance.now() - decodeStart) / 1000;
          row = { dataset: dataset.name, mode, iteration, bytes, archive_bytes: statSync(archivePath).size,
            compression_seconds: compression, decompression_seconds: decompression,
            compression_mib_s: bytes / 1048576 / compression, decompression_mib_s: bytes / 1048576 / decompression };
          rmSync(tar); if (archivePath !== archive) rmSync(archivePath);
        }
        checkTree(output, dataset.records);
        row.sha256_roundtrip = true;
        row.ratio = bytes / row.archive_bytes;
        results.rows.push(row);
        writeFileSync(resultFile, JSON.stringify(results, null, 2) + '\n');
        rmSync(output, { recursive: true });
        rmSync(archive, { force: true });
        console.log(JSON.stringify(row));
      }
    }
  }
} finally { rmSync(temp, { recursive: true, force: true }); }
