export function resolveParentOrigin(config, requested, ownOrigin) {
  const valid = value => {
    try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) && url.origin === value ? value : null; } catch { return null; }
  };
  if (config.parentOrigin != null) {
    if (!valid(config.parentOrigin)) throw new Error('parentOrigin에 정확한 HTTP(S) origin을 지정하세요.');
    return config.parentOrigin;
  }
  return valid(requested) && [ownOrigin, ...(config.allowedParentOrigins ?? [])].includes(requested) ? requested : null;
}

// Result payload is frozen at completion. Retries always send that same payload.
export class GameBridge {
  constructor({ origin, parent, ownWindow, configure, start, onDelivery = () => {} }) {
    Object.assign(this, { origin, parent, ownWindow, configure, start, onDelivery });
    this.sessionId = null; this.result = null; this.initialized = false; this.acked = false; this.attempts = 0;
  }
  send(data) { if (this.origin && this.parent !== this.ownWindow) this.parent.postMessage(data, this.origin); }
  ready() { this.send({ type: 'card-game:ready', version: 1, gameId: 'term-match' }); }
  receive(event) {
    if (!this.origin || event.origin !== this.origin || event.source !== this.parent) return false;
    const d = event.data;
    if (!d || d.version !== 1) return false;
    if (d.type === 'card-game:ack' && this.result && d.sessionId === this.sessionId) {
      this.acked = true; this.onDelivery('acknowledged'); return true;
    }
    if (d.type !== 'card-game:init' || this.initialized) return false;
    if (typeof d.sessionId !== 'string' || !d.sessionId || d.sessionId.length > 128 || !d.config || typeof d.config !== 'object' || Array.isArray(d.config)) return false;
    try { this.configure(d.config); }
    catch (error) { this.send({ type: 'card-game:error', version: 1, sessionId: d.sessionId, message: error.message }); return false; }
    this.sessionId = d.sessionId; this.initialized = true; this.start(d.sessionId); return true;
  }
  publish(result, at) {
    if (!this.result) this.result = result;
    if (!this.origin || this.parent === this.ownWindow) return;
    if (!this.acked && this.attempts < 5 && (this.lastSent == null || at - this.lastSent >= 1000)) {
      this.send(this.result); this.lastSent = at; this.attempts++; this.onDelivery('sent');
    }
    if (!this.acked && !this.unconfirmed && this.attempts === 5 && at - this.lastSent >= 1000) { this.unconfirmed = true; this.onDelivery('unconfirmed'); }
  }
}
