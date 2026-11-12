import { readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
process.chdir(fileURLToPath(new URL('../',import.meta.url)));
const modules=new Map();
async function visit(path) {
  path=resolve(path);if(modules.has(path))return;modules.set(path,'');
  let source=await readFile(path,'utf8');
  for(const match of source.matchAll(/^import .*? from '([^']+)';$/gm)) await visit(resolve(dirname(path),match[1]));
  source=source.replace(/^import .*? from '[^']+';\r?\n/gm,'').replace(/^export /gm,'');
  modules.set(path,source);
}
await visit('src/app.js');
// Dependencies have been traversed, so emit by a separate postorder traversal.
const emitted=new Set();let code='';
async function emit(path){path=resolve(path);if(emitted.has(path))return;emitted.add(path);const source=await readFile(path,'utf8');for(const m of source.matchAll(/^import .*? from '([^']+)';$/gm))await emit(resolve(dirname(path),m[1]));code+='\n'+modules.get(path);}
await emit('src/app.js');
let html=await readFile('index.html','utf8');
const css=await readFile('src/app.css','utf8');
html=html.replace('<link rel="stylesheet" href="./src/app.css">',()=>`<style>${css}</style>`)
  .replace('<script type="module" src="./src/app.js"></script>',()=>`<script>(()=>{${code.replaceAll('</script','<\\/script')}\n})();</script>`);
await rm('dist',{recursive:true,force:true});
await mkdir('dist',{recursive:true});
await writeFile('dist/standalone.html',html);
await writeFile('dist/index.html',html);
console.log(`Typing: ${Buffer.byteLength(html)} bytes; React/Vite/TypeScript runtime: 0`);
