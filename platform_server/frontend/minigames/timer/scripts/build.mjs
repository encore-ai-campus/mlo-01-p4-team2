import { mkdir, copyFile, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const files = ['index.html', 'styles.css', 'app.js', 'engine.js', 'config.js', 'host.html', 'host.js', 'assets/mark.svg'];
await mkdir(resolve(root, 'dist/assets'), { recursive: true });
for (const file of files) await copyFile(resolve(root, file), resolve(root, 'dist', file));

// 패키저 의존성 없이, 같은 소스로 에셋까지 포함한 단일 HTML도 생성합니다.
const read = (file) => readFile(resolve(root, file), 'utf8');
const [html, css, svg, config, engine, app] = await Promise.all([
  read('index.html'), read('styles.css'), read('assets/mark.svg'),
  read('config.js'), read('engine.js'), read('app.js'),
]);
const script = [config, engine, app]
  .map((source) => source.replace(/^import .*;\n/gm, '').replace(/^export /gm, ''))
  .join('\n');
const standalone = html
  .replace('<link rel="stylesheet" href="./styles.css">', () => `<style>${css}</style>`)
  .replace('  <script type="module" src="./app.js"></script>', '')
  .replaceAll('./assets/mark.svg', `data:image/svg+xml,${encodeURIComponent(svg)}`)
  .replace('</body>', () => `<script>(() => {\n${script.replaceAll('</script', '<\\/script')}\n})();</script>\n</body>`);
await writeFile(resolve(root, 'dist/standalone.html'), standalone);
console.log(`dist/에 정적 에셋 ${files.length}개와 standalone.html을 생성했습니다. 외부 의존성 없음.`);
