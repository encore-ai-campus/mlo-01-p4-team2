"""pipeline/silver/strict.py — 엄격 JSON 검사(Spark 없음). 같은 함수를 Spark UDF 로 부르는 시험은 spark_contracts.py."""
import json
import unittest

from pipeline.silver import strict

INT39 = "123456789012345678901234567890123456789"


class DecodeJson(unittest.TestCase):
    def assert_decode_error(self, value):
        with self.assertRaises(strict.InvalidRecord) as caught:
            strict.decode_json(value, "value")
        self.assertEqual(caught.exception.code, "decode")

    def test_object_from_bytes_and_text(self):
        self.assertEqual(strict.decode_json(b'{"a": [1, 2.5, null, "\xea\xb0\x80"]}', "value"), ('{"a": [1, 2.5, null, "가"]}', {"a": [1, 2.5, None, "가"]}))
        self.assertEqual(strict.decode_json('{"a":1}', "raw_data"), ('{"a":1}', {"a": 1}))
        self.assertEqual(strict.decode_json(bytearray(b"{}"), "value"), ("{}", {}))

    def test_invalid_utf8(self):
        self.assert_decode_error(b'{"a": "\xff"}')

    def test_duplicate_keys_at_any_depth(self):
        self.assert_decode_error(b'{"a": 1, "a": 1}')
        self.assert_decode_error('{"a": {"b": 1, "b": 2}}')

    def test_non_finite_and_overflowing_numbers(self):
        for text in ('{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}', '{"a": 1e999}', '{"a": 1' + "0" * 4300 + '}'):
            with self.subTest(text=text[:20]):
                self.assert_decode_error(text)

    def test_lone_surrogate(self):
        self.assert_decode_error('{"a": "\\ud800"}')

    def test_top_level_must_be_object(self):
        for text in ("[]", "1", '"a"', "null"):
            with self.subTest(text=text):
                self.assert_decode_error(text)

    def test_not_json_or_not_bytes(self):
        for value in (b"", b"{", '{"a":1} x', "{'a': 1}", None, 1, ["{}"], '{"a":' * 100000):
            with self.subTest(value=str(value)[:20]):
                self.assert_decode_error(value)

    def test_large_integers_pass_exactly_and_reals_are_read_as_binary64(self):
        """39자리·1,000자리(jsonb 정규화) 정수는 그대로 통과한다(Python int). 실수는 binary64 로 읽힌다 — 아래로 넘치는 수는 0.0, 유효숫자 17자리를
        넘는 차이는 같은 값(알려진 한계, Planner 결정: 바꾸지 않음 — strict 머리말)."""
        _, parsed = strict.decode_json('{"a": ' + INT39 + '}', "value")
        self.assertEqual(str(parsed["a"]), INT39)
        _, parsed = strict.decode_json('{"a": 1' + "0" * 999 + '}', "value")
        self.assertEqual(len(str(parsed["a"])), 1000)
        for text in ('{"a": 1e-400}', '{"a": 0.' + "0" * 399 + '1}'):
            with self.subTest(text=text[:16]):
                self.assertEqual(strict.decode_json(text, "value")[1], {"a": 0.0})
        self.assertEqual(strict.decode_json('{"a": 0.12345678901234567890}', "value")[1], strict.decode_json('{"a": 0.12345678901234567891}', "value")[1])


class StrictReasons(unittest.TestCase):
    NESTED = (("after", "raw_data"), ("after", "payload_json"))

    def envelope(self, after):
        return json.dumps({"op": "c", "after": after}).encode("utf-8")

    def test_good_envelope_with_nested_json_strings(self):
        value = self.envelope({"raw_data": '{"user_uuid": "u"}', "payload_json": '{"raw_data": {}}'})
        self.assertEqual(strict.strict_reasons(value, self.NESTED), (None, None))

    def test_envelope_reason_comes_alone(self):
        for value, needle in ((b'{"a":"\xff"}', "UTF-8"), (b'{"a":1,"a":1}', "겹친다"), (None, "바이트·문자열이 아니다"), (b"[1]", "최상위")):
            with self.subTest(needle=needle):
                outer, inner = strict.strict_reasons(value, self.NESTED)
                self.assertIn(needle, outer)
                self.assertIsNone(inner)  # 봉투가 틀리면 안쪽은 보지 않는다

    def test_nested_strings_are_checked_with_the_same_rules_and_reported_apart(self):
        cases = {'{"a":1,"a":2}': "겹친다", '{"a":NaN}': "유한하지", '[1]': "최상위", '{"a":"\\ud800"}': "JSON 문서", '{"a":1} x': "JSON 문서"}
        for inner, needle in cases.items():
            with self.subTest(inner=inner):
                outer, reason = strict.strict_reasons(self.envelope({"raw_data": inner}), self.NESTED)
                self.assertIsNone(outer)  # 봉투는 정상 — 어댑터는 출처·op 확인(③) 뒤에 이 사유를 쓴다(④)
                self.assertIn("after.raw_data", reason)
                self.assertIn(needle, reason)
        outer, reason = strict.strict_reasons(self.envelope({"raw_data": "{}", "payload_json": "x"}), self.NESTED)
        self.assertEqual((outer, "after.payload_json" in reason), (None, True))

    def test_only_paths_asked_and_only_strings_are_checked(self):
        bad = '{"a":1,"a":2}'
        self.assertEqual(strict.strict_reasons(self.envelope({"raw_data": bad})), (None, None))  # 경로를 주지 않으면 보지 않는다
        for after in ({"raw_data": None}, {}, {"raw_data": {"a": 1}}, {"raw_data": 1}):  # 없음·null·객체·수는 계약 검사가 본다
            with self.subTest(after=after):
                self.assertEqual(strict.strict_reasons(self.envelope(after), self.NESTED), (None, None))
        self.assertEqual(strict.strict_reasons(json.dumps({"after": None}), self.NESTED), (None, None))


if __name__ == "__main__":
    unittest.main()
