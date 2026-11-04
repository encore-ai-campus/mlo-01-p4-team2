function key(base, shifted, finger) {
    return { id: /^[a-z]$/.test(base) ? `Key${base.toUpperCase()}` : `Char${base.charCodeAt(0)}`,
        label: /^[a-z]$/.test(base) ? base.toUpperCase() : base, base, shifted, finger };
}
export const keyboardRows = [
    [key('`', '~', 1), key('1', '!', 1), key('2', '@', 2), key('3', '#', 3), key('4', '$', 4), key('5', '%', 4),
        key('6', '^', 7), key('7', '&', 7), key('8', '*', 8), key('9', '(', 9), key('0', ')', 10),
        key('-', '_', 10), key('=', '+', 10), { id: 'Backspace', label: '⌫', finger: 10, width: 2 }],
    [{ id: 'Tab', label: 'Tab', finger: 1, width: 1.5 }, key('q', undefined, 1), key('w', undefined, 2), key('e', undefined, 3),
        key('r', undefined, 4), key('t', undefined, 4), key('y', undefined, 7), key('u', undefined, 7), key('i', undefined, 8),
        key('o', undefined, 9), key('p', undefined, 10), key('[', '{', 10), key(']', '}', 10), key('\\', '|', 10)],
    [{ id: 'CapsLock', label: 'Caps', finger: 1, width: 1.75 }, key('a', undefined, 1), key('s', undefined, 2), key('d', undefined, 3),
        key('f', undefined, 4), key('g', undefined, 4), key('h', undefined, 7), key('j', undefined, 7), key('k', undefined, 8),
        key('l', undefined, 9), key(';', ':', 10), key("'", '"', 10), { id: 'Enter', label: 'Enter ↵', base: '\n', finger: 10, width: 2.25 }],
    [{ id: 'ShiftLeft', label: 'Shift', finger: 1, width: 2.25 }, key('z', undefined, 1), key('x', undefined, 2), key('c', undefined, 3),
        key('v', undefined, 4), key('b', undefined, 4), key('n', undefined, 7), key('m', undefined, 7), key(',', '<', 8),
        key('.', '>', 9), key('/', '?', 10), { id: 'ShiftRight', label: 'Shift', finger: 10, width: 2.75 }],
    [{ id: 'ControlLeft', label: 'Ctrl', finger: 1, width: 1.25 }, { id: 'MetaLeft', label: '⌘', finger: 1, width: 1.25 },
        { id: 'AltLeft', label: 'Alt', finger: 5, width: 1.25 }, { id: 'Space', label: 'Space', base: ' ', finger: 6, width: 6.25 },
        { id: 'AltRight', label: 'Alt', finger: 6, width: 1.25 }, { id: 'MetaRight', label: '⌘', finger: 10, width: 1.25 },
        { id: 'ControlRight', label: 'Ctrl', finger: 10, width: 1.25 }],
];
export const keyboardKeys = keyboardRows.flat();
export const fingerNames = {
    1: '왼손 새끼손가락', 2: '왼손 약지', 3: '왼손 중지', 4: '왼손 검지', 5: '왼손 엄지',
    6: '오른손 엄지', 7: '오른손 검지', 8: '오른손 중지', 9: '오른손 약지', 10: '오른손 새끼손가락',
};
export function findKeyboardKey(char) {
    return keyboardKeys.find((item) => item.base === char || item.shifted === char
        || (/^[A-Z]$/.test(char) && item.base === char.toLowerCase()));
}
export function keyboardGuide(expected, correcting = false) {
    const target = correcting ? keyboardKeys.find((item) => item.id === 'Backspace') : expected ? findKeyboardKey(expected) : undefined;
    const needsShift = !correcting && !!expected && (!!target?.shifted && target.shifted === expected || /^[A-Z]$/.test(expected));
    const shiftId = needsShift && target ? (target.finger <= 5 ? 'ShiftRight' : 'ShiftLeft') : undefined;
    const shift = keyboardKeys.find((item) => item.id === shiftId);
    return { target, shift, description: target
            ? `${fingerNames[target.finger]}${shift ? ` + ${fingerNames[shift.finger]} Shift` : ''}` : '다음 입력을 기다리고 있어요.' };
}
/** Fold A/a and shifted punctuation onto their shared physical QWERTY key. */
export function aggregateKeyMistakes(mistakes) {
    const result = new Map();
    for (const [char, count] of Object.entries(mistakes)) {
        const physical = findKeyboardKey(char);
        if (!physical || !Number.isFinite(count) || count <= 0)
            continue;
        const entry = result.get(physical.id) ?? { key: physical, count: 0, characters: {} };
        entry.count += count;
        entry.characters[char] = count;
        result.set(physical.id, entry);
    }
    return result;
}
