export const problems = [
  {"id":"py-beginner-01","version":1,"language":"python","difficulty":"beginner","title":"Hello, Python","description":"변수를 만들고, 첫 인사를 출력해 보세요.","code":"name = \"Ada\"\nprint(f\"Hello, {name}!\")"},
  {"id":"py-beginner-02","version":1,"language":"python","difficulty":"beginner","title":"작은 계산기","description":"기본 연산자와 변수 이름에 익숙해져요.","code":"price = 1200\nquantity = 3\ntotal = price * quantity\nprint(total)"},
  {"id":"py-beginner-03","version":1,"language":"python","difficulty":"beginner","title":"조건을 읽는 법","description":"조건식과 네 칸 들여쓰기를 연습해요.","code":"temperature = 24\nif temperature >= 20:\n    print(\"A warm day\")\nelse:\n    print(\"Bring a jacket\")"},
  {"id":"py-beginner-04","version":1,"language":"python","difficulty":"beginner","title":"문자열 조각","description":"대괄호와 문자열 슬라이싱을 연습해요.","code":"message = \"Keep learning\"\nfirst_word = message[:4]\nprint(first_word.upper())"},
  {"id":"py-beginner-05","version":1,"language":"python","difficulty":"beginner","title":"목록에 담기","description":"리스트를 만들고 값을 추가해요.","code":"colors = [\"mint\", \"blue\", \"gold\"]\ncolors.append(\"coral\")\nprint(len(colors))"},
  {"id":"py-intermediate-01","version":1,"language":"python","difficulty":"intermediate","title":"한 걸음씩 반복","description":"반복문과 enumerate의 구조를 익혀요.","code":"topics = [\"syntax\", \"loops\", \"functions\"]\nfor index, topic in enumerate(topics, start=1):\n    print(f\"{index}. {topic}\")"},
  {"id":"py-intermediate-02","version":1,"language":"python","difficulty":"intermediate","title":"함수의 경계","description":"함수 선언, 조건 분기와 반환문을 입력해요.","code":"def average(values):\n    if not values:\n        return 0\n    return sum(values) / len(values)\n\nprint(average([4, 7, 10]))"},
  {"id":"py-intermediate-03","version":1,"language":"python","difficulty":"intermediate","title":"한 줄의 변환","description":"리스트 컴프리헨션의 괄호와 조건을 연습해요.","code":"numbers = range(1, 11)\nsquares = [n ** 2 for n in numbers if n % 2 == 0]\nprint(squares)"},
  {"id":"py-intermediate-04","version":1,"language":"python","difficulty":"intermediate","title":"이름과 값","description":"딕셔너리와 항목 순회를 연습해요.","code":"scores = {\"Ada\": 92, \"Linus\": 87}\nfor name, score in scores.items():\n    label = \"pass\" if score >= 80 else \"retry\"\n    print(name, label)"},
  {"id":"py-intermediate-05","version":1,"language":"python","difficulty":"intermediate","title":"실패를 다루기","description":"try와 except의 들여쓰기를 익혀요.","code":"def parse_number(text):\n    try:\n        return int(text)\n    except ValueError:\n        return None\n\nprint(parse_number(\"42\"))"},
  {"id":"py-advanced-01","version":1,"language":"python","difficulty":"advanced","title":"필요할 때 하나씩","description":"제너레이터와 복합 조건을 연습해요.","code":"def even_squares(limit):\n    for value in range(limit):\n        if value > 0 and value % 2 == 0:\n            yield value ** 2\n\nprint(list(even_squares(10)))"},
  {"id":"py-advanced-02","version":1,"language":"python","difficulty":"advanced","title":"중첩된 조건","description":"중첩 반복과 여러 단계의 들여쓰기를 입력해요.","code":"def find_pairs(values, target):\n    pairs = []\n    for left in range(len(values)):\n        for right in range(left + 1, len(values)):\n            if values[left] + values[right] == target:\n                pairs.append((values[left], values[right]))\n    return pairs"},
  {"id":"py-advanced-03","version":1,"language":"python","difficulty":"advanced","title":"함수를 감싸는 함수","description":"데코레이터와 가변 인자를 연습해요.","code":"def announce(func):\n    def wrapper(*args, **kwargs):\n        print(f\"Running {func.__name__}\")\n        return func(*args, **kwargs)\n    return wrapper\n\n@announce\ndef greet(name):\n    return f\"Hello, {name}\""},
  {"id":"py-advanced-04","version":1,"language":"python","difficulty":"advanced","title":"작은 객체의 설계","description":"클래스, 메서드와 상태 변경을 입력해요.","code":"class Counter:\n    def __init__(self, start=0):\n        self.value = start\n\n    def increment(self, step=1):\n        self.value += step\n        return self.value\n\ncounter = Counter()\nprint(counter.increment())"},
  {"id":"py-advanced-05","version":1,"language":"python","difficulty":"advanced","title":"데이터를 재구성하기","description":"복합 자료와 정렬 키 함수를 연습해요.","code":"records = [(\"Ada\", 92), (\"Linus\", 87), (\"Grace\", 95)]\nranked = sorted(records, key=lambda item: item[1], reverse=True)\nresult = {\n    name: {\"score\": score, \"rank\": rank}\n    for rank, (name, score) in enumerate(ranked, start=1)\n}\nprint(result)"}
]
;

export function pickBeginnerProblem(pool, random = Math.random) {
  const beginners = pool.filter(problem => problem.difficulty === 'beginner');
  if (!beginners.length) throw new Error('입문 문제가 필요해요.');
  return beginners[Math.floor(random() * beginners.length)];
}
