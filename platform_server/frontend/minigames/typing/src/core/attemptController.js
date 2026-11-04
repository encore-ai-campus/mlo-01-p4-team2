import { createTypingState, transition } from './typingEngine.js';
import { calculateMetrics } from './metrics.js';
const browserClock = { monotonic: () => performance.now(), utc: () => new Date().toISOString() };
export function newId() {
    if (typeof crypto.randomUUID === 'function')
        return crypto.randomUUID();
    // LAN HTTP lacks randomUUID (secure-context only); getRandomValues still works.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
export class AttemptController {
    id = newId();
    problem;
    state;
    listeners = new Set();
    startedAt = null;
    endedAt = null;
    terminalSent = false;
    startedSent = false;
    sink;
    context;
    sessionId;
    clock;
    constructor(problem, mode, sink, context, sessionId, clock = browserClock) {
        this.problem = problem;
        this.state = createTypingState(mode);
        this.sink = sink;
        this.context = { ...context };
        this.sessionId = context.sessionId ?? sessionId;
        this.clock = clock;
    }
    getSnapshot = () => this.state;
    subscribe = (listener) => {
        this.listeners.add(listener);
        return () => { this.listeners.delete(listener); };
    };
    notify() { for (const listener of this.listeners)
        listener(); }
    metrics = () => calculateMetrics(this.state, this.problem.code.length, this.startedAt === null ? 0 : (this.endedAt ?? this.clock.monotonic()) - this.startedAt);
    input = (action) => {
        if (this.state.status === 'completed' || this.state.status === 'abandoned')
            return;
        if (action.type === 'append' && action.text && !this.startedSent) {
            this.startedAt = this.clock.monotonic();
            this.startedSent = true;
            this.state = { ...this.state, status: 'running' };
            this.emit('code_typing.started');
        }
        this.state = transition(this.state, this.problem.code, action);
        if (this.state.status === 'completed' && !this.terminalSent) {
            this.terminalSent = true;
            this.endedAt = this.clock.monotonic();
            this.emit('code_typing.completed');
        }
        this.notify();
    };
    abandon = (reason) => {
        if (!this.startedSent || this.terminalSent)
            return;
        this.terminalSent = true;
        this.endedAt = this.clock.monotonic();
        this.state = { ...this.state, status: 'abandoned' };
        this.emit('code_typing.abandoned', reason);
        this.notify();
    };
    emit(type, reason) {
        const event = {
            schema_version: 2, event_id: newId(), event_type: type, occurred_at: this.clock.utc(),
            attempt_id: this.id, session_id: this.sessionId, user_id: this.context.userId ?? null,
            class_id: this.context.classId ?? null, content_id: this.problem.id,
            content_version: this.problem.version, language: this.problem.language,
            difficulty: this.problem.difficulty, typing_mode: this.state.mode,
            metric_policy: 'speedcoder-v1-manual-whitespace', metrics: this.metrics(),
            ...(reason ? { abandon_reason: reason } : {}),
        };
        try {
            void Promise.resolve(this.sink.emit(event)).catch(() => undefined);
        }
        catch { /* Analytics must never interrupt practice, including synchronous failures. */ }
    }
}
