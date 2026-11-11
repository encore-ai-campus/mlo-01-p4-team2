import { TERMS } from './terms.js';
import { createSessionId } from './ids.js';

export const RULE_KEYS = ['timeLimitMs', 'totalRounds', 'requiredWins', 'maxMistakes', 'rows', 'columns', 'topic', 'previewMs', 'flipStaggerMs', 'flipDurationMs', 'mismatchMs', 'roundTransitionMs'];
const bounds = {
  timeLimitMs: [100, 3600000], totalRounds: [1, 100], requiredWins: [1, 100],
  maxMistakes: [1, 1000], columns: [4, 12], previewMs: [0, 60000],
  flipStaggerMs: [0, 200], flipDurationMs: [0, 2000], mismatchMs: [0, 10000], roundTransitionMs: [0, 10000],
};
export function validateConfig(input) {
  if (!input || typeof input !== 'object') throw new TypeError('설정 객체가 필요합니다.');
  const config = Object.fromEntries(RULE_KEYS.map(key => [key, input[key]]));
  for (const [key, [min, max]] of Object.entries(bounds)) {
    if (!Number.isInteger(config[key]) || config[key] < min || config[key] > max) throw new RangeError(`${key}: ${min}~${max} 사이 정수를 입력하세요.`);
  }
  if (config.rows !== 4) throw new RangeError('rows는 4로 설정하세요.');
  if (!['mixed', 'python', 'sql'].includes(config.topic)) throw new RangeError('topic: mixed, python, sql 중 선택하세요.');
  if (config.requiredWins > config.totalRounds) throw new RangeError('통과 횟수는 전체 라운드 수 이하여야 합니다.');
  if (termPool(config).length < config.rows * config.columns / 2) throw new RangeError('카드 수에 비해 용어가 부족합니다.');
  return Object.freeze(config);
}
function termPool(config) { return TERMS.filter(t => config.topic === 'mixed' || t.category.toLowerCase() === config.topic); }
export function shuffle(values, random = Math.random) {
  const result = [...values];
  for (let i = result.length - 1; i > 0; i--) { const j = Math.floor(random() * (i + 1)); [result[i], result[j]] = [result[j], result[i]]; }
  return result;
}
export class MemoryGame {
  constructor(config, { now = () => performance.now(), random = Math.random, id = createSessionId } = {}) {
    this.now = now; this.random = random; this.id = id;
    this.configure(config);
  }
  configure(config) {
    if (this.phase && this.phase !== 'idle' && this.phase !== 'complete') throw new Error('진행 중에는 설정을 바꿀 수 없습니다.');
    this.config = validateConfig(config); this.phase = 'idle'; this.rounds = []; this.cards = []; this.result = null;
    this.serverDeck = config.deck ?? null;
    if (this.serverDeck && (!Array.isArray(this.serverDeck) || this.serverDeck.length !== 16
        || this.serverDeck.some(i => !Number.isInteger(i) || !TERMS[i])
        || new Set(this.serverDeck).size !== 8
        || this.serverDeck.some(i => this.serverDeck.filter(v => v === i).length !== 2))) throw new Error('카드 배치를 확인해 주세요.');
    this.selected = []; this.matched = []; this.mistakes = 0; this.lastMatch = null; this.roundIndex = null;
  }
  start(sessionId = this.id()) {
    if (this.phase !== 'idle' && this.phase !== 'complete') return false;
    if (typeof sessionId !== 'string' || !sessionId || sessionId.length > 128) throw new Error('유효한 sessionId가 필요합니다.');
    this.sessionId = sessionId; this.startedAt = new Date().toISOString(); this.rounds = []; this.result = null;
    this.beginRound(this.now()); return true;
  }
  beginRound(at) {
    this.roundIndex = this.rounds.length + 1;
    const terms = shuffle(termPool(this.config), this.random).slice(0, this.config.rows * this.config.columns / 2);
    this.cards = shuffle(terms.flatMap(t => [{ id: `${t.id}-a`, term: t }, { id: `${t.id}-b`, term: t }]), this.random);
    if (this.serverDeck) this.cards = this.serverDeck.map((i, index) => ({id: `${i}-${index}`, term: TERMS[i]}));
    this.picks = [];
    this.selected = []; this.matched = []; this.mistakes = 0; this.lastMatch = null; this.pendingUntil = null;
    this.previewStarted = at;
    this.playStarted = at + this.config.previewMs + (this.cards.length - 1) * this.config.flipStaggerMs + this.config.flipDurationMs;
    this.deadline = this.playStarted + this.config.timeLimitMs; this.phase = 'preview';
  }
  tick(at = this.now()) {
    if (this.phase === 'preview' && at >= this.playStarted) this.phase = 'playing';
    if (this.phase !== 'playing') return;
    // Deadline takes precedence, including a second selection exactly at the limit.
    if (at >= this.deadline) { this.finishRound(false, 'timeout', at); return; }
    if (this.pendingUntil !== null && at >= this.pendingUntil) { this.pendingUntil = null; this.selected = []; }
  }
  continueRound() {
    if (this.phase !== 'transition') return false;
    this.beginRound(this.now()); return true;
  }
  choose(index) {
    const at = this.now(); this.tick(at);
    if (this.phase !== 'playing' || this.pendingUntil !== null || !Number.isInteger(index) || !this.cards[index]
      || this.selected.includes(index) || this.matched.includes(this.cards[index].term.id)) return { type: 'ignored' };
    this.selected.push(index);
    if (this.selected.length < 2) return { type: 'selected' };
    const [a, b] = this.selected.map(i => this.cards[i]);
    this.picks.push([...this.selected, Math.round(at - this.playStarted)]);
    if (a.term.id === b.term.id) {
      this.matched.push(a.term.id); this.lastMatch = a.term; this.selected = [];
      if (this.matched.length === this.cards.length / 2) this.finishRound(true, 'all_matched', at);
      return { type: 'match', term: a.term };
    }
    this.mistakes++;
    if (this.mistakes >= this.config.maxMistakes) this.finishRound(false, 'mistake_limit', at);
    else this.pendingUntil = at + this.config.mismatchMs;
    return { type: 'mismatch' };
  }
  finishRound(success, reason, at) {
    if (this.phase !== 'playing') return;
    this.rounds.push(Object.freeze({ round: this.roundIndex, success, reason, mistakes: this.mistakes,
      matchedPairs: this.matched.length, totalPairs: this.cards.length / 2,
      elapsedMs: Math.min(this.config.timeLimitMs, Math.max(0, Math.round(at - this.playStarted))) }));
    if (this.rounds.length < this.config.totalRounds) { this.phase = 'transition'; return; }
    const wins = this.rounds.filter(r => r.success).length;
    this.phase = 'complete';
    this.result = Object.freeze({ type: 'card-game:result', version: 1, gameId: 'term-match', sessionId: this.sessionId,
      success: wins >= this.config.requiredWins, wins, losses: this.rounds.length - wins,
      totalRounds: this.config.totalRounds, requiredWins: this.config.requiredWins,
      rules: this.config, rounds: Object.freeze([...this.rounds]), picks: this.picks.map(p => Object.freeze([...p])), startedAt: this.startedAt, completedAt: new Date().toISOString() });
  }
  remainingMs(at = this.now()) {
    if (this.phase === 'idle' || this.phase === 'preview') return this.config.timeLimitMs;
    if (this.phase === 'transition' || this.phase === 'complete') return this.config.timeLimitMs - this.rounds.at(-1).elapsedMs;
    return Math.max(0, this.deadline - at);
  }
  faceUp(index, at = this.now()) {
    if (this.phase === 'preview') return at < this.previewStarted + this.config.previewMs + index * this.config.flipStaggerMs;
    return this.selected.includes(index) || this.matched.includes(this.cards[index]?.term.id);
  }
}
