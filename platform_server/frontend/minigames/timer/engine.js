export function validateConfig(config) {
  const { targetMs, toleranceMs, overtimeMs } = config;
  if (!Number.isFinite(targetMs) || targetMs < 100 || targetMs > 300000) {
    throw new RangeError('목표 시간은 0.1초 이상 300초 이하여야 합니다.');
  }
  if (!Number.isFinite(toleranceMs) || toleranceMs < 0 || toleranceMs >= targetMs) {
    throw new RangeError('허용 오차는 0 이상, 목표 시간 미만이어야 합니다.');
  }
  if (!Number.isFinite(overtimeMs) || overtimeMs < 0 || overtimeMs > 60000) {
    throw new RangeError('초과 대기 시간은 0초 이상 60초 이하여야 합니다.');
  }
  return Object.freeze({ targetMs, toleranceMs, overtimeMs });
}

export function evaluate(elapsedMs, config) {
  if (!Number.isFinite(elapsedMs) || elapsedMs < 0) {
    throw new RangeError('경과 시간은 0 이상의 유한한 값이어야 합니다.');
  }
  // 화면과 같은 0.01초 단위로 판정하고 저장한다.
  elapsedMs = Math.round(elapsedMs / 10) * 10;
  const errorMs = elapsedMs - config.targetMs;
  const success = elapsedMs >= config.targetMs - config.toleranceMs
    && elapsedMs <= config.targetMs + config.toleranceMs;
  return Object.freeze({ elapsedMs, errorMs, success });
}

function createRoundId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  // 사설 IP의 HTTP에서는 randomUUID가 없지만 getRandomValues는 사용할 수 있습니다.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export class TimerGame {
  constructor(config, { now = () => performance.now(), id = createRoundId } = {}) {
    this.now = now;
    this.id = id;
    this.configure(config);
  }

  configure(config) {
    if (this.state === 'running') throw new Error('진행 중에는 설정을 변경할 수 없습니다.');
    this.config = validateConfig(config);
    this.state = 'idle';
    this.result = null;
  }

  start() {
    if (this.state === 'running') return false;
    this.roundId = this.id();
    this.startedAt = this.now();
    this.state = 'running';
    this.result = null;
    return true;
  }

  elapsed() {
    return this.state === 'running'
      ? Math.max(0, this.now() - this.startedAt)
      : (this.result?.elapsedMs ?? 0);
  }

  finish(reason = 'stopped') {
    if (this.state !== 'running') return null;
    const timing = evaluate(this.elapsed(), this.config);
    this.state = 'finished';
    this.result = Object.freeze({
      ...timing,
      success: reason === 'stopped' && timing.success,
      reason,
      roundId: this.roundId,
      targetMs: this.config.targetMs,
      toleranceMs: this.config.toleranceMs,
    });
    return this.result;
  }

  checkTimeout() {
    if (this.state !== 'running') return null;
    if (this.elapsed() > this.config.targetMs + this.config.toleranceMs + this.config.overtimeMs) {
      return this.finish('timeout');
    }
    return null;
  }
}

export function resultMessage(result) {
  return Object.freeze({
    type: 'timer-game:result',
    version: 1,
    roundId: result.roundId,
    success: result.success,
  });
}

export function resolveParentOrigin(config, requested, ownOrigin) {
  const exactOrigin = (value) => {
    if (typeof value !== 'string') return null;
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) && url.origin === value ? value : null;
    } catch { return null; }
  };
  if (config.parentOrigin != null) {
    const origin = exactOrigin(config.parentOrigin);
    if (!origin) throw new Error('parentOrigin에는 http(s)의 정확한 origin을 지정하세요.');
    return origin;
  }
  const origin = exactOrigin(requested);
  const allowed = [ownOrigin, ...(config.allowedParentOrigins ?? [])];
  return origin && allowed.includes(origin) ? origin : null;
}
