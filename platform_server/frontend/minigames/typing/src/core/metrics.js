import { remainingErrors } from './typingEngine.js';
export function calculateMetrics(state, targetChars, elapsedMs) {
    const elapsed = Math.max(0, Number.isFinite(elapsedMs) ? elapsedMs : 0);
    const n = state.cursor;
    const errors = remainingErrors(state);
    const penalty = state.mode === 'natural' ? errors : state.forcedErrorEpisodes;
    const minutes = Math.max(1, Math.floor(elapsed / 1000)) / 60;
    return {
        elapsed_ms: elapsed,
        target_chars: targetChars,
        progressed_chars: n,
        remaining_error_chars: errors,
        mistake_count: state.mistakeCount,
        forced_error_episodes: state.forcedErrorEpisodes,
        accuracy_pct: n ? Math.max(0, Math.min(100, (n - penalty) / n * 100)) : null,
        wpm: !n || !elapsed ? 0 : Math.max(0, Math.round((n / 5 - (state.mode === 'natural' ? errors : 0)) / minutes)),
        cpm: !n || !elapsed ? 0 : n / minutes,
        progress_pct: targetChars > 0 ? n / targetChars * 100 : 0,
    };
}
export function formatDuration(ms) {
    const centiseconds = Math.round(Math.max(0, Number.isFinite(ms) ? ms : 0) / 10);
    const minutes = Math.floor(centiseconds / 6000);
    const seconds = (centiseconds % 6000) / 100;
    return `${String(minutes).padStart(2, '0')}:${seconds.toFixed(2).padStart(5, '0')}`;
}
