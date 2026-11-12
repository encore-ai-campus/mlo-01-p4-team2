const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const test = require('node:test');

class Element {
  constructor() {
    this.children = []; this.dataset = {}; this.listeners = {};
    this.textContent = this.value = ''; this.hidden = false;
    this.classList = {add() {}, remove() {}, toggle() {}};
  }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  click() { for (const fn of this.listeners.click ?? []) fn({preventDefault() {}}); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  querySelectorAll() { return []; }
  setAttribute() {}
  reset() {}
  focus() {}
}

const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(setImmediate); };

test('board read retries retain their UUID and the next successful visit gets a new UUID on HTTP', async () => {
  const elements = new Map(), reads = [];
  const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  let failRead = true;
  const item = {id: 7, title: '게시글', category: 'free', categoryLabel: '자유', body: '본문',
    author: {id: 'user', nickname: '학생'}, commentCount: 0, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString()};
  const pagination = {page: 1, pages: 1, total: 1, startIndex: 1};
  const window = {addEventListener() {}, matchMedia: () => ({matches: false})};
  const context = vm.createContext({window, URLSearchParams, AbortController,
    // LAN HTTP has getRandomValues but may have no randomUUID.
    crypto: {getRandomValues: webcrypto.getRandomValues.bind(webcrypto)},
    document: {getElementById: element, createElement: () => new Element(), createTextNode: text => text},
    async fetch(raw) {
      const url = new URL(raw, 'http://campus.test');
      let body;
      if (url.pathname.endsWith('/posts/7/')) {
        reads.push(url.searchParams.get('read_uuid'));
        if (failRead) { failRead = false; throw new Error('Connection lost after saving'); }
        body = {post: item};
      } else if (url.pathname.endsWith('/comments/')) body = {comments: [], pagination: {...pagination, total: 0}};
      else if (url.pathname.endsWith('/posts/')) body = {posts: [item], pagination};
      else body = {user: {nickname: '학생'}, categories: [], total: 1};
      return {ok: true, status: 200, headers: {get: () => 'application/json'}, json: async () => body};
    }});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../dist/community.js'), 'utf8'), context);
  window.CampusCommunity.create({onBack() {}}).open();
  await settle();
  const link = () => element('communityPostRows').children[0].children[1].children[0];
  link().click(); await settle();
  link().click(); await settle();
  assert.equal(reads.length, 2);
  assert.equal(reads[0], reads[1]);
  assert.match(reads[0], /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  element('communityListBack').click(); await settle();
  link().click(); await settle();
  assert.equal(reads.length, 3);
  assert.notEqual(reads[1], reads[2]);
  assert.equal(element('communityPostTitle').textContent, item.title);
});

test('list numbers follow filtered page positions while open, edit and delete keep the post ID', async () => {
  const elements = new Map(), requests = [];
  const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  let items = Array.from({length: 14}, (_, index) => ({
    id: 9000 - index * 17, title: index ? '번호 테스트 ' + index : 'IT 글',
    category: index ? 'free' : 'it', categoryLabel: index ? '자유' : 'IT', body: '본문',
    author: {id: 'author', nickname: '작성자'}, canEdit: true, canDelete: true, commentCount: 0,
    createdAt: '2026-11-10T01:00:00Z', updatedAt: '2026-11-10T01:00:00Z',
  }));
  let pageSize = 12;
  const window = {addEventListener() {}, matchMedia: () => ({matches: false}), confirm: () => true};
  const context = vm.createContext({window, URLSearchParams, AbortController, crypto: webcrypto,
    document: {getElementById: element, createElement: () => new Element(), createTextNode: text => text,
      querySelector: () => ({value: 'test-csrf'})},
    async fetch(raw, options) {
      const url = new URL(raw, 'http://campus.test');
      requests.push({url, method: options.method});
      let body;
      const detail = url.pathname.match(/\/posts\/(\d+)\/$/);
      if (detail) {
        const id = Number(detail[1]), item = items.find(item => item.id === id);
        assert.ok(item, 'request must use an existing post ID');
        if (options.method === 'DELETE') { items = items.filter(item => item.id !== id); body = {deleted: true}; }
        else {
          if (options.method === 'PATCH') Object.assign(item, JSON.parse(options.body));
          body = {post: {...item}};
        }
      } else if (url.pathname.endsWith('/comments/')) {
        body = {comments: [], pagination: {page: 1, pages: 1, total: 0}};
      } else if (url.pathname.endsWith('/posts/')) {
        const category = url.searchParams.get('category'), search = url.searchParams.get('q');
        const filtered = items.filter(item => (category === 'all' || item.category === category) && item.title.includes(search));
        const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
        const page = Math.min(Number(url.searchParams.get('page')), pages), offset = (page - 1) * pageSize;
        body = {posts: filtered.slice(offset, offset + pageSize),
          pagination: {page, pages, total: filtered.length, startIndex: filtered.length ? offset + 1 : 0}};
      } else {
        body = {user: {id: 'author', nickname: '작성자'}, total: items.length,
          categories: ['free', 'it'].map(id => ({id, name: id, canWrite: true, count: items.filter(item => item.category === id).length}))};
      }
      return {ok: true, status: 200, headers: {get: () => 'application/json'}, json: async () => body};
    }});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../dist/community.js'), 'utf8'), context);
  window.CampusCommunity.create({onBack() {}}).open();
  await settle();
  const rows = () => element('communityPostRows').children;
  const numbers = () => rows().filter(row => row.children[0].className === 'community-number').map(row => row.children[0].textContent);
  const next = async () => { element('communityPagination').children.at(-1).click(); await settle(); };
  const select = async id => { element('communityTabs').children.find(tab => tab.dataset.category === id).click(); await settle(); };
  const submit = async id => {
    for (const handler of element(id).listeners.submit) handler({preventDefault() {}});
    await settle();
  };
  assert.deepEqual(numbers(), Array.from({length: 12}, (_, index) => String(index + 1)));
  assert.equal(rows()[0].dataset.postId, '9000');
  await next();
  assert.deepEqual(numbers(), ['13', '14']);
  await select('free');
  assert.equal(numbers()[0], '1');
  await next();
  assert.deepEqual(numbers(), ['13']);
  element('communitySearchInput').value = '번호 테스트 12';
  await submit('communitySearch');
  assert.deepEqual(numbers(), ['1']);
  assert.equal(rows()[0].dataset.postId, String(9000 - 12 * 17));
  element('communitySearchInput').value = '없는 검색어';
  await submit('communitySearch');
  assert.deepEqual(numbers(), []);
  assert.equal(rows().length, 1);
  element('communitySearchReset').click(); await settle();
  assert.equal(numbers()[0], '1');
  await next();
  const lastId = Number(rows()[0].dataset.postId);
  assert.deepEqual(numbers(), ['13']);
  rows()[0].children[1].children[0].click(); await settle();
  assert.ok(requests.some(request => request.method === 'GET' && request.url.pathname === `/api/community/posts/${lastId}/`));
  element('communityEdit').click(); await settle();
  element('communityTitle').value = '수정한 글';
  await submit('communityEditor');
  assert.ok(requests.some(request => request.method === 'PATCH' && request.url.pathname === `/api/community/posts/${lastId}/`));
  element('communityDelete').click(); await settle();
  assert.ok(requests.some(request => request.method === 'DELETE' && request.url.pathname === `/api/community/posts/${lastId}/`));
  assert.deepEqual(numbers(), Array.from({length: 12}, (_, index) => String(index + 1)));
  await select('it');
  assert.deepEqual(numbers(), ['1']);
  assert.equal(rows()[0].dataset.postId, '9000');
  pageSize = 5;
  await select('all'); await next();
  assert.deepEqual(numbers(), ['6', '7', '8', '9', '10']);
});
