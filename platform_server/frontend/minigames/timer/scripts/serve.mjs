import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const port = Number(process.env.PORT ?? 4173);
const host = process.env.HOST ?? '127.0.0.1';
const files = new Map([
  ['index.html', 'text/html; charset=utf-8'],
  ['host.html', 'text/html; charset=utf-8'],
  ['styles.css', 'text/css; charset=utf-8'],
  ...['app.js', 'engine.js', 'config.js', 'host.js'].map((file) => [file, 'text/javascript; charset=utf-8']),
  ['assets/mark.svg', 'image/svg+xml'],
]);
const server = createServer(async (request, response) => {
  const headers = { 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store' };
  if (!['GET', 'HEAD'].includes(request.method)) {
    response.writeHead(405, { ...headers, Allow: 'GET, HEAD' }).end('Method not allowed');
    return;
  }
  try {
    const path = new URL(request.url, 'http://localhost').pathname;
    const file = path === '/' ? 'index.html' : path.slice(1);
    if (!files.has(file)) { response.writeHead(404, headers).end('Not found'); return; }
    const content = await readFile(resolve(root, file));
    response.writeHead(200, { ...headers, 'Content-Type': files.get(file) });
    response.end(request.method === 'HEAD' ? undefined : content);
  } catch {
    response.writeHead(500, headers).end('Unable to serve asset');
  }
});
server.on('error', (error) => { console.error(`서버 실행 실패: ${error.code}`); process.exitCode = 1; });
server.listen(port, host, () => console.log(`ON TIME  http://${host}:${port}\n연동 예제 http://${host}:${port}/host.html`));
