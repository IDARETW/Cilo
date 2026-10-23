// Measure a fixed corpus against an archive byte budget; never pads/truncates files.
// Usage: node tools/benchmark-capacity.mjs SOURCE RESULTS.json [REPEATS=3] [BUDGET=1000000000]
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, readdirSync, statSync, mkdtempSync, rmSync, mkdirSync } from 'node:fs';
import { join, resolve, dirname, relative, isAbsolute } from 'node:path';
import { tmpdir, cpus, totalmem, platform, release } from 'node:os';

const [source, destination, repeatArg = '3', budgetArg = '1000000000'] = process.argv.slice(2);
if (!source || !destination) throw Error('Expected SOURCE RESULTS.json [REPEATS] [BUDGET]');
const repeats = Number(repeatArg), budget = Number(budgetArg);
if (!Number.isInteger(repeats) || repeats < 1 || repeats > 10 || !Number.isSafeInteger(budget) || budget < 1)
  throw Error('Invalid repeat count or byte budget');
const root = resolve(source), binary = resolve(process.env.CILO_BINARY ?? 'target/release/cilo');
const outputRelative = relative(root, resolve(destination));
if (outputRelative !== '..' && !outputRelative.startsWith('../') && !outputRelative.startsWith('..\\') && !isAbsolute(outputRelative))
  throw Error('Write benchmark results outside the source corpus');
const digest = path => createHash('sha256').update(readFileSync(path)).digest('hex');
function inventory(directory) {
  return readdirSync(directory, { recursive: true, withFileTypes: true }).filter(e => e.isFile()).map(e => {
    const path = join(e.parentPath ?? e.path, e.name);
    return { path: path.slice(directory.length + 1).replaceAll('\\', '/'), bytes: statSync(path).size, sha256: digest(path) };
  }).sort((a, b) => a.path.localeCompare(b.path));
}
const files = inventory(root);
const result = {
  measured_at_utc: new Date().toISOString(), platform: platform(), kernel: release(),
  cpu: cpus()[0].model, logical_cpus: cpus().length, memory_bytes: totalmem(),
  binary: { version: execFileSync(binary, ['--version'], { encoding: 'utf8' }).trim(), sha256: digest(binary) },
  profile: 'balanced', threads: 4, budget_bytes: budget, repeats,
  input_bytes: files.reduce((sum, file) => sum + file.bytes, 0), files, rows: [],
  notes: 'Fixed selected corpus, whole files only; no synthetic padding. Warm source cache, sequential runs on a shared host. CLI timings include hashes, I/O and sync. Network and independent SHA-256 comparison excluded. Capacity depends on this corpus; no claim of an optimal subset or a whole-build ratio.',
};
const run = args => JSON.parse(execFileSync(binary, args, { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 }));
mkdirSync(dirname(resolve(destination)), { recursive: true });
const temporary = mkdtempSync(join(tmpdir(), 'cilo-capacity-benchmark-'));
try {
  for (let iteration = 1; iteration <= repeats; iteration++) {
    const archive = join(temporary, 'sample.cilo'), output = join(temporary, 'restored');
    const pack = run(['pack', root, archive, '--profile', 'balanced', '--threads', '4']);
    if (pack.archive_bytes > budget) throw Error(`Archive exceeds budget: ${pack.archive_bytes} > ${budget}`);
    const unpack = run(['unpack', archive, output]);
    if (JSON.stringify(inventory(output)) !== JSON.stringify(files)) throw Error('Independent SHA-256 roundtrip mismatch');
    const row = { iteration, ...pack, headroom_bytes: budget - pack.archive_bytes,
      decompression_seconds: unpack.elapsed_seconds, decompression_mib_s: unpack.throughput_mib_s,
      blocks_decoded: unpack.blocks_decoded, sha256_roundtrip: true };
    result.rows.push(row);
    writeFileSync(destination, JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(row));
    rmSync(archive); rmSync(output, { recursive: true });
  }
} finally { rmSync(temporary, { recursive: true, force: true }); }
