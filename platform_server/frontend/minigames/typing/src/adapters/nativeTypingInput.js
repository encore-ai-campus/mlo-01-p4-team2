import { englishKey } from '../core/keyboard.js';
/** Restricted tail input, scoped to one textarea. No global keyboard listeners. */
export function attachTypingInput(element, input, readValue, escape) {
    let composing = false;
    let compositionCommit = null;
    let handledKey = false;
    let composingFromKey = false;
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
    const append = (text) => {
        if (typeof text === 'string' && /^[\x20-\x7e\n]+$/.test(text))
            input({ type: 'append', text });
        restore();
    };
    const acceptKey = (event, action) => {
        event.preventDefault();
        handledKey = true;
        composingFromKey ||= composing;
        composing = false;
        input(action);
        restore();
    };
    const keydown = (event) => {
        if (!event.cancelable) return;
        handledKey = false;
        compositionCommit = null;
        const key = event.code || event.key;
        if (event.metaKey || event.ctrlKey || event.altKey) {
            const shortcut = /^Key[A-Z]$/.test(key) ? key.slice(3).toLowerCase() : event.key.toLowerCase();
            if (['v','x','z','y','a','backspace','delete','arrowleft','arrowright'].includes(shortcut))
                event.preventDefault();
            return;
        }
        if (key === 'Escape') {
            event.preventDefault(); composing = false; restore(); escape(); return;
        }
        if (key === 'Tab') {
            if (!event.shiftKey) acceptKey(event, {type:'append', text:'    '});
            return;
        }
        if (key === 'Backspace') {
            acceptKey(event, {type:'backspace'}); return;
        }
        if (key === 'Enter' || key === 'NumpadEnter') {
            acceptKey(event, {type:'append', text:'\n'}); return;
        }
        if (['Delete','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown'].includes(key)) {
            event.preventDefault(); restore(); return;
        }
        const text = englishKey(event) ?? event.key;
        if (text.length === 1) {
            event.preventDefault();
            if (/^[\x20-\x7e]$/.test(text)) acceptKey(event, {type:'append', text});
        }
    };
    const keyup = () => { handledKey = false; };
    const beforeinput = (event) => {
        if (handledKey) {
            if (event.cancelable) event.preventDefault();
            restore(); return;
        }
        if (composing || event.isComposing)
            return;
        // Some browser/IME paths deliver a non-cancelable beforeinput. Let the DOM
        // finish that edit and consume it once from input instead of overwriting it.
        if (!event.cancelable)
            return;
        event.preventDefault();
        if (event.inputType === 'insertText' && event.data) {
            if (compositionCommit === event.data) {
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
        if (handledKey) { restore(); return; }
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
    const compositionstart = () => { composing = true; composingFromKey = handledKey; };
    const compositionend = (event) => {
        const alreadyTyped = handledKey || composingFromKey;
        composing = false; composingFromKey = false;
        compositionCommit = event.data || null;
        if (!alreadyTyped) append(event.data);
        else restore();
        // Only suppress the immediate browser echo, never a later repeated letter.
        queueMicrotask(() => { compositionCommit = null; });
    };
    const prevent = (event) => { event.preventDefault(); };
    element.addEventListener('keydown', keydown);
    element.addEventListener('keyup', keyup);
    element.addEventListener('blur', keyup);
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
        element.removeEventListener('keyup', keyup);
        element.removeEventListener('blur', keyup);
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
