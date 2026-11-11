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
  const pagination = {page: 1, pages: 1, total: 1};
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
