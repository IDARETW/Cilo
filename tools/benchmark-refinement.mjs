// Native-archiver comparison harness. Node is used only to drive/verify benchmarks.
// Usage: node tools/benchmark-refinement.mjs DATASETS.json RESULTS.json OLD_CILO [REPEATS=3]
// DATASETS.json: [{"name":"sample","root":"/path/to/files","profiles":["fast","balanced","max"]}]
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, readdirSync, statSync, mkdtempSync, rmSync, mkdirSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { tmpdir, cpus, totalmem, platform, release } from 'node:os';

const [config, resultPath, oldBinary, repeatsArg = '3'] = process.argv.slice(2);
if (!config || !resultPath || !oldBinary) throw Error('Expected DATASETS.json RESULTS.json OLD_CILO [REPEATS]');
const repeats = Number(repeatsArg);
if (!Number.isInteger(repeats) || repeats < 1 || repeats > 10) throw Error('repeats must be 1..10');
const digest = path => createHash('sha256').update(readFileSync(path)).digest('hex');
const binaries = { previous: resolve(oldBinary), current: resolve('target/release/cilo') };
const run = (bin, args) => JSON.parse(execFileSync(bin, args, { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 }));
function inventory(root) {
  return readdirSync(root, { recursive: true, withFileTypes: true }).filter(e => e.isFile()).map(e => {
    const path = join(e.parentPath ?? e.path, e.name);
    return { path: path.slice(root.length + 1).replaceAll('\\', '/'), bytes: statSync(path).size, sha256: digest(path) };
  }).sort((a, b) => a.path.localeCompare(b.path));
}
const result = {
  measured_at_utc: new Date().toISOString(), platform: platform(), kernel: release(),
  cpu: cpus()[0].model, logical_cpus: cpus().length, memory_bytes: totalmem(), threads: 4, repeats,
  binaries: Object.fromEntries(Object.entries(binaries).map(([name, bin]) => [name, {
    version: execFileSync(bin, ['--version'], { encoding: 'utf8' }).trim(), sha256: digest(bin) }])),
  notes: 'Warm source cache, sequential runs, alternating version order, shared cloud host. Default block/chunk sizes for each version. CLI times include hashes, I/O and sync. Independent SHA-256 validation excluded from timings. No network time.',
  datasets: [], rows: [],
};
mkdirSync(dirname(resolve(resultPath)), { recursive: true });
const temporary = mkdtempSync(join(tmpdir(), 'cilo-benchmark-'));
try {
  for (const dataset of JSON.parse(readFileSync(config))) {
    const root = resolve(dataset.root), expected = inventory(root);
    const inputBytes = expected.reduce((sum, e) => sum + e.bytes, 0);
    result.datasets.push({ name: dataset.name, input_bytes: inputBytes, files: expected });
    for (const profile of dataset.profiles ?? ['fast', 'balanced', 'max']) {
      for (let iteration = 1; iteration <= repeats; iteration++) {
        for (const version of iteration % 2 ? ['previous', 'current'] : ['current', 'previous']) {
          const archive = join(temporary, 'sample.cilo'), output = join(temporary, 'restored');
          const pack = run(binaries[version], ['pack', root, archive, '--profile', profile, '--threads', '4']);
          const unpack = run(binaries[version], ['unpack', archive, output]);
          if (JSON.stringify(inventory(output)) !== JSON.stringify(expected)) throw Error('SHA-256 roundtrip mismatch');
          const row = { dataset: dataset.name, version, profile, iteration, ...pack,
            decompression_seconds: unpack.elapsed_seconds, decompression_mib_s: unpack.throughput_mib_s,
            blocks_decoded: unpack.blocks_decoded, sha256_roundtrip: true };
          result.rows.push(row);
          writeFileSync(resultPath, JSON.stringify(result, null, 2) + '\n');
          console.log(JSON.stringify(row));
          rmSync(archive); rmSync(output, { recursive: true });
        }
      }
    }
  }
} finally { rmSync(temporary, { recursive: true, force: true }); }
