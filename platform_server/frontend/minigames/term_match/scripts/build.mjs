import {mkdir,readFile,writeFile} from 'node:fs/promises';
let html=await readFile('index.html','utf8');
const css=await readFile('styles.css','utf8'),icon=await readFile('assets/mark.svg','utf8');
const code=(await Promise.all(['config.js','terms.js','ids.js','engine.js','bridge.js','app.js'].map(f=>readFile(f,'utf8')))).join('\n').replace(/^import .+ from '.+';\s*$/gm,'').replace(/^export /gm,'');
html=html.replace('<link rel="stylesheet" href="./styles.css">',()=>'<style>'+css+'</style>').replace('href="./assets/mark.svg"','href="data:image/svg+xml,'+encodeURIComponent(icon)+'"').replace('<script type="module" src="./app.js"></script>',()=>'<script type="module">'+code.replaceAll('</script','<\\/script')+'</script>');
await mkdir('dist',{recursive:true});await writeFile('dist/standalone.html',html);
