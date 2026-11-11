export function createTypingState(mode) {
    return { status: 'idle', mode, cursor: 0, slots: [], activeErrorIndex: null,
        mistakeCount: 0, forcedErrorEpisodes: 0, mistakeByExpectedChar: {} };
}
export function remainingErrors(state) {
    return state.slots.filter((slot) => slot.status === 'wrong' || slot.status === 'pending').length;
}
/** All scoring decisions live here. Input adapters never derive scores from DOM. */
export function transition(previous, target, action) {
    if (!target.length || previous.status === 'completed' || previous.status === 'abandoned')
        return previous;
    if (action.type === 'backspace') {
        if (!previous.cursor && previous.activeErrorIndex === null)
            return previous;
        const stuckAtLast = previous.activeErrorIndex === previous.cursor;
        const cursor = stuckAtLast ? previous.cursor : Math.max(0, previous.cursor - 1);
        return { ...previous, cursor, slots: previous.slots.slice(0, cursor),
            activeErrorIndex: previous.activeErrorIndex !== null && cursor > previous.activeErrorIndex
                ? previous.activeErrorIndex : null };
    }
    if (!action.text)
        return previous;
    let state = previous;
    // UTF-16 positions agree with the editor. Initial problem data is ASCII.
    for (const typed of action.text.split('')) {
        if (state.status === 'completed' || state.cursor >= target.length)
            break;
        const index = state.cursor;
        const correct = typed === target[index];
        const forced = state.mode === 'forced';
        const inError = state.activeErrorIndex !== null;
        const lastException = forced && inError && index === target.length - 1 && correct;
        const slots = [...state.slots];
        if (forced && inError && !lastException) {
            if (index >= Math.min(state.activeErrorIndex + 5, target.length - 1))
                continue;
            slots[index] = { typed, status: 'pending' };
            state = { ...state, cursor: index + 1, slots };
            continue;
        }
        slots[index] = { typed, status: correct ? 'correct' : 'wrong' };
        const mistake = !correct;
        const cursor = forced && mistake ? Math.min(index + 1, target.length - 1) : index + 1;
        state = {
            ...state,
            slots,
            cursor,
            status: cursor >= target.length ? 'completed' : 'running',
            activeErrorIndex: forced && mistake ? index : null,
            mistakeCount: state.mistakeCount + Number(mistake),
            forcedErrorEpisodes: state.forcedErrorEpisodes + Number(forced && mistake),
            mistakeByExpectedChar: mistake
                ? { ...state.mistakeByExpectedChar, [target[index]]: (state.mistakeByExpectedChar[target[index]] ?? 0) + 1 }
                : state.mistakeByExpectedChar,
        };
    }
    return state;
}
