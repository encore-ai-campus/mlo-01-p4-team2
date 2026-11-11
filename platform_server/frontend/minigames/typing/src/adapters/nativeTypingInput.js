/** Restricted tail input, scoped to one textarea. No global keyboard listeners. */
export function attachTypingInput(element, input, readValue, escape) {
    let composing = false;
    let compositionCommit = null;
    const restore = () => {
        if (composing)
            return;
        const value = readValue();
        if (element.value !== value)
            element.value = value;
        if (element.selectionStart !== value.length || element.selectionEnd !== value.length) {
            element.setSelectionRange(value.length, value.length);
        }
    };
    const append = (text) => { input({ type: 'append', text }); restore(); };
    const keydown = (event) => {
        if (composing || event.isComposing || event.keyCode === 229 || !event.cancelable)
            return;
        compositionCommit = null;
        if (event.key === 'Escape') {
            event.preventDefault();
            restore();
            escape();
            return;
        }
        if (event.key === 'Tab') {
            // Shift+Tab remains a standard focus escape, in addition to Escape.
            if (event.shiftKey)
                return;
            event.preventDefault();
            append('    ');
            return;
        }
        if (event.metaKey || event.ctrlKey || event.altKey) {
            // Copy and browser shortcuts remain available; editing shortcuts do not.
            if (['v', 'x', 'z', 'y', 'a', 'Backspace', 'Delete', 'ArrowLeft', 'ArrowRight'].includes(event.key.toLowerCase())
                || ['Backspace', 'Delete', 'ArrowLeft', 'ArrowRight'].includes(event.key))
                event.preventDefault();
            return;
        }
        if (event.key === 'Backspace') {
            event.preventDefault();
            input({ type: 'backspace' });
            restore();
            return;
        }
        if (event.key === 'Enter') {
            event.preventDefault();
            append('\n');
            return;
        }
        if (['Delete', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'].includes(event.key)) {
            event.preventDefault();
            restore();
            return;
        }
        if (event.key.length === 1) {
            event.preventDefault();
            append(event.key);
        }
    };
    const beforeinput = (event) => {
        if (composing || event.isComposing)
            return;
        // Some browser/IME paths deliver a non-cancelable beforeinput. Let the DOM
        // finish that edit and consume it once from input instead of overwriting it.
        if (!event.cancelable)
            return;
        event.preventDefault();
        if (event.inputType === 'insertText' && event.data) {
            if (compositionCommit === event.data) {
                compositionCommit = null;
                restore();
                return;
            }
            append(event.data);
        }
        else if (event.inputType === 'deleteContentBackward') {
            input({ type: 'backspace' });
            restore();
        }
        else if (event.inputType === 'insertLineBreak')
            append('\n');
        else
            restore();
    };
    const nativeInput = (event) => {
        const edit = event;
        if (composing || edit.isComposing)
            return;
        const previous = readValue();
        const value = element.value;
        if (value === previous)
            return;
        // Accept real tail edits when keydown/beforeinput was skipped (e.g. 229).
        // All other DOM replacements, paste and history edits are still rejected.
        if (edit.inputType === 'insertText' && edit.data) {
            if (compositionCommit === edit.data) {
                compositionCommit = null;
                restore();
                return;
            }
            append(edit.data);
        }
        else if (edit.inputType === 'insertText' && value.startsWith(previous) && value.length > previous.length) {
            append(value.slice(previous.length));
        }
        else if (edit.inputType === 'deleteContentBackward' && previous.length > 0 && value === previous.slice(0, -1)) {
            input({ type: 'backspace' });
            restore();
        }
        else
            restore();
    };
    const compositionstart = () => { composing = true; };
    const compositionend = (event) => {
        composing = false;
        compositionCommit = event.data || null;
        if (event.data)
            input({ type: 'append', text: event.data });
        restore();
        // Only suppress the immediate browser echo, never a later repeated letter.
        queueMicrotask(() => { compositionCommit = null; });
    };
    const prevent = (event) => { event.preventDefault(); };
    element.addEventListener('keydown', keydown);
    element.addEventListener('beforeinput', beforeinput);
    element.addEventListener('input', nativeInput);
    element.addEventListener('select', restore);
    element.addEventListener('pointerup', restore);
    element.addEventListener('focus', restore);
    element.addEventListener('compositionstart', compositionstart);
    element.addEventListener('compositionend', compositionend);
    for (const type of ['paste', 'drop', 'cut', 'dragstart'])
        element.addEventListener(type, prevent);
    return () => {
        element.removeEventListener('keydown', keydown);
        element.removeEventListener('beforeinput', beforeinput);
        element.removeEventListener('input', nativeInput);
        element.removeEventListener('select', restore);
        element.removeEventListener('pointerup', restore);
        element.removeEventListener('focus', restore);
        element.removeEventListener('compositionstart', compositionstart);
        element.removeEventListener('compositionend', compositionend);
        for (const type of ['paste', 'drop', 'cut', 'dragstart'])
            element.removeEventListener(type, prevent);
    };
}
