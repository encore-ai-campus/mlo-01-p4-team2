const keywords = new Set('and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield'.split(' '));
const builtins = new Set('print len range enumerate sum list dict int str sorted zip set float'.split(' '));
/** Small lexical highlighter for the fixed ASCII Python curriculum; never evaluates code. */
export function pythonCharacterClasses(code) {
    const classes = Array(code.length).fill('');
    const tokens = /#[^\n]*|(?:[fFrRbuBU]{0,2})(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|\b[A-Za-z_]\w*\b|\b\d+(?:\.\d+)?\b/g;
    for (const match of code.matchAll(tokens)) {
        const token = match[0];
        const kind = token.startsWith('#') ? 'comment' : /["']/.test(token) ? 'string'
            : /^\d/.test(token) ? 'number' : keywords.has(token) ? 'keyword' : builtins.has(token) ? 'builtin' : '';
        classes.fill(kind, match.index, match.index + token.length);
    }
    return classes;
}
