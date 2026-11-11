export function normalizeCode(code) {
    return code.replace(/\r\n?/g, '\n');
}
export function validateProblems(problems) {
    const seen = new Set();
    return problems.map((problem) => {
        const key = `${problem.id}@${problem.version}`;
        if (!problem.id || !Number.isInteger(problem.version) || problem.version < 1 || seen.has(key)) {
            throw new Error('문제 ID와 버전은 유효하고 고유해야 합니다.');
        }
        if (problem.language !== 'python' || !['beginner', 'intermediate', 'advanced'].includes(problem.difficulty)) {
            throw new Error('지원하지 않는 문제 형식입니다.');
        }
        const code = normalizeCode(problem.code);
        if (!code.length || /[^\x20-\x7e\n]/.test(code)) {
            throw new Error('문제는 비어 있지 않은 ASCII 코드와 공백 들여쓰기를 사용해야 합니다.');
        }
        seen.add(key);
        return { ...problem, code };
    });
}
