import { fileURLToPath } from 'node:url';
process.env.STATIC_DIRECTORY = fileURLToPath(new URL('../dist/',import.meta.url));
process.env.PORT ??= '4178';
await import('../../scripts/serve.mjs');
