import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
const bridge=await readFile(root+'minigames/channel.js','utf8');
await mkdir(root+'dist/minigames',{recursive:true});
for(const game of ['timer','typing','term_match']){
 const result=spawnSync(process.execPath,['scripts/build.mjs'],{cwd:root+'minigames/'+game,stdio:'inherit'});
 if(result.status!==0)process.exit(result.status??1);
 let html=await readFile(root+'minigames/'+game+'/dist/standalone.html','utf8');
 if(game==='typing'){const icon=await readFile(root+'minigames/typing/public/favicon.svg','utf8');html=html.replaceAll('./favicon.svg','data:image/svg+xml,'+encodeURIComponent(icon));}
 html=html.replace('</head>',()=>'<script>'+bridge.replace('__GAME_ID__',game)+'</script></head>');
 await writeFile(root+'dist/minigames/'+game+'.html',html);
}
