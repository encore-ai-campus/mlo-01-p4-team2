/* Reuse startup images and keep downloaded games inert until the player opens them. */
(() => {
  'use strict';
  const images = new Set(), games = new Map();
  const gamePaths = [
    ...['timer', 'typing', 'term_match'].map(name => `minigames/${name}.html?v=20261105-typing-replay-v1`),
    'event-games/sql-memory.html', 'event-games/card.html',
  ];
  const key = path => new URL(path, document.baseURI).href;
  const get = id => document.getElementById(id);

  function track(image) { images.add(image); }

  function imageReady(image, retry) {
    return new Promise((resolve, reject) => {
      const cleanup = () => { image.removeEventListener('load', loaded); image.removeEventListener('error', failed); };
      const failed = () => { cleanup(); reject(new Error('이미지를 불러오지 못했습니다.')); };
      const loaded = async () => {
        cleanup();
        try {
          if (!image.naturalWidth) throw new Error('이미지를 불러오지 못했습니다.');
          if (image.decode) await image.decode();
          resolve();
        } catch (error) { reject(error); }
      };
      image.addEventListener('load', loaded);
      image.addEventListener('error', failed);
      if (retry) image.src = image.src;
      else if (image.complete) image.naturalWidth ? loaded() : failed();
    });
  }

  async function prepare(avatar) {
    if (window.CampusEntry) {
      const retry = get('entryRetry'), status = get('entryStatus');
      while (true) {
        try {
          for (const path of gamePaths) {
            const url = key(path);
            if (games.has(url)) continue;
            status.textContent = '게임 파일을 준비하고 있어요…';
            const response = await fetch(url, {credentials:'same-origin', cache:'no-cache'});
            if (!response.ok || response.redirected || !response.headers.get('content-type')?.includes('text/html'))
              throw new Error('게임 파일을 불러오지 못했어요. 다시 시도해 주세요.');
            games.set(url, {source:await response.text(), url});
          }
          return;
        } catch (error) {
          status.textContent = error.message; retry.hidden = false;
          await new Promise(resolve => { retry.onclick = resolve; });
          retry.hidden = true;
        }
      }
    }

    for (const image of document.images) {
      if (image.loading !== 'lazy' && image.src && !/\/assets\/(?:achievements|roof-slides)\//.test(image.src)) track(image);
    }
    for (const path of ['assets/kkuwang-chat-happy.png',
                        `assets/characters/${avatar.startsWith('female-') ? 'female' : 'male'}-face.png`]) {
      const image = new Image(); image.src = path; track(image);
    }
    const unique = new Map();
    for (const image of images) if (image.src && !image.src.startsWith('data:')) unique.set(key(image.src), image);
    const jobs = [...unique.values()].map(image => ({ run: retry => imageReady(image, retry) }));
    const gameJobs = gamePaths.map(path => ({ run: async () => {
      const url = key(path), response = await fetch(url, { credentials: 'same-origin', cache: 'no-cache' });
      if (!response.ok || response.redirected || !response.headers.get('content-type')?.includes('text/html'))
        throw new Error('게임 파일을 불러오지 못했습니다.');
      games.set(url, { source: await response.text(), url });
    } }));
    if (document.fonts) jobs.push({ run: () => document.fonts.ready });
    const all = [...jobs, ...gameJobs], retry = get('campusLoadingRetry');
    const update = () => {
      const percent = Math.floor(all.filter(job => job.done).length / all.length * 100);
      get('campusLoadingProgress').setAttribute('aria-valuenow', String(percent));
      get('campusLoadingProgress').style.setProperty('--loading-progress', `${percent}%`);
      get('campusLoadingPercent').textContent = `${percent}%`;
    };
    update();
    return new Promise(resolve => {
      let running = false;
      async function attempt() {
        if (running) return;
        running = true; retry.hidden = true; get('campusLoadingError').hidden = true;
        const run = async job => {
          if (job.done) return;
          try { await job.run(job.attempted); job.done = true; update(); }
          catch { /* Keep the gate closed; retry only the files that failed. */ }
          job.attempted = true;
        };
        await Promise.all([Promise.all(jobs.map(run)), (async () => { for (const job of gameJobs) await run(job); })()]);
        running = false;
        if (all.some(job => !job.done)) {
          get('campusLoadingError').hidden = false; retry.hidden = false; retry.focus();
          return;
        }
        get('campusLoadingDetail').textContent = '캠퍼스를 준비하고 있어요.';
        // Paint the completed gauge before creating the campus renderer.
        await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
        resolve();
      }
      retry.onclick = attempt;
      attempt();
    });
  }

  function gameSource(path) {
    const game = games.get(key(path));
    if (!game) throw new Error('게임 파일이 준비되지 않았습니다. 새로고침해 주세요.');
    return game.source;
  }

  function gameURL(path) {
    const game = games.get(key(path));
    if (!game) throw new Error('게임 파일이 준비되지 않았습니다. 새로고침해 주세요.');
    if (!game.blobURL) {
      const base = new URL('.', game.url).href;
      const source = game.source.replace(/<head(?:\s[^>]*)?>/i, head => `${head}<base href="${base}">`);
      game.blobURL = URL.createObjectURL(new Blob([source], { type: 'text/html;charset=utf-8' }));
    }
    return game.blobURL;
  }

  window.addEventListener('campus:ready', () => {
    if (window.CampusEntry) return;
    document.documentElement.classList.remove('campus-loading');
    document.querySelector('.workspace')?.removeAttribute('inert');
    get('campusLoading').setAttribute('aria-busy', 'false');
    get('campusLoading').hidden = true;
  }, { once: true });
  window.CampusLoading = { track, prepare, gameSource, gameURL };
})();
