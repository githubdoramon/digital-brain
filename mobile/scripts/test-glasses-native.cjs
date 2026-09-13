/* Compile the two Android modules first; exercises their Kotlin bytecode on the host JVM. */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const cache = path.join(
  process.env.GRADLE_USER_HOME || path.join(os.homedir(), '.gradle'),
  'caches/modules-2/files-2.1/org.jetbrains.kotlin/kotlin-stdlib',
);
function jars(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    return entry.isDirectory() ? jars(target) : target.endsWith('.jar') ? [target] : [];
  });
}
const runtime =
  jars(cache)
    .sort()
    .find((file) => file.includes('/2.1.20/')) || jars(cache).sort().at(-1);
if (!runtime)
  throw new Error('Build the Android modules first to populate the Kotlin runtime cache.');
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'glasses-native-contracts-'));
const classpath = [
  path.join(root, 'modules/digital-brain-glasses-alerts/android/build/tmp/kotlin-classes/debug'),
  path.join(root, 'node_modules/@mentra/bluetooth-sdk/android/build/tmp/kotlin-classes/debug'),
  runtime,
].join(path.delimiter);
try {
  execFileSync(
    'javac',
    ['-cp', classpath, '-d', output, path.join(__dirname, 'GlassesNativeContracts.java')],
    { stdio: 'inherit' },
  );
  execFileSync(
    'java',
    ['-cp', [output, classpath].join(path.delimiter), 'GlassesNativeContracts'],
    { stdio: 'inherit' },
  );
} finally {
  fs.rmSync(output, { recursive: true, force: true });
}
