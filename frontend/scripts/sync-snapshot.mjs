import { mkdir, copyFile, readFile, writeFile } from 'node:fs/promises';
const root = new URL('../../', import.meta.url);
const destination = new URL('../public/snapshot/', import.meta.url);
await mkdir(destination, { recursive: true });
for (const name of ['graph', 'meta', 'cards', 'top_check', 'top_block', 'sankey', 'resilience']) {
  await copyFile(new URL(`out/web/${name}.json`, root), new URL(`${name}.json`, destination));
}
const check = JSON.parse(await readFile(new URL('out/brief_check.json', root), 'utf8'));
await writeFile(new URL('brief.json', destination), JSON.stringify({ ...check, cached: true, markdown: await readFile(new URL('out/brief.md', root), 'utf8') }));
// Keep the initial render synchronized with the same pipeline snapshot.
for (const name of ['graph', 'meta', 'top_check']) await copyFile(new URL(`${name}.json`, destination), new URL(`../public/${name}.json`, import.meta.url));
console.log('Pipeline snapshot synchronized.');
