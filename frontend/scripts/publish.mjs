import { cp, mkdir, readdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const source = fileURLToPath(new URL('../out/', import.meta.url));
const destination = path.resolve(fileURLToPath(new URL('../../web/dist/', import.meta.url)));
await mkdir(destination, { recursive: true });
// This directory is generated exclusively by this build. Never remove its parent.
for (const entry of await readdir(destination)) {
  const target = path.resolve(destination, entry);
  if (!target.startsWith(destination + path.sep)) throw new Error('Invalid export target');
  await rm(target, { recursive: true, force: true });
}
await cp(source, destination, { recursive: true });
console.log('Static frontend ready in web/dist (served by python -m mycelium.serve).');
