"""옛 판 파일 정리 잡(계획 temp/pipeline/45_vacuum-plan.md r2 — DEP3-VAC)의 드라이버 쪽 판단: 표준 라이브러리와 pipeline.* 만 쓴다(Spark 없이 시험).

- 설정(기본값 없음): `VACUUM_LAKE_ROOT`(Silver·Gold 와 같은 뿌리). `s3a://` 뿌리면 S3 넷(Silver control.s3_settings — 키는 파일에서만).
- 대상(§3.1): 원천 하나의 Silver 세대마다 넷(events·errors·reconcile·quarantine — checkpoint 는 Delta 표가 아님)과 포인터,
  원천 Gold 폴더 아래 표 폴더 전부의 판(v<N>)과 정정 기록. 경로는 기존 규칙 함수(Silver silver_paths·pointer_path, Gold table_path·corrections_path)로만 만들고,
  나열할 부모 폴더도 그 함수의 결과에서 마지막 조각을 떼어 얻는다. 다른 원천의 폴더는 열지 않는다.
- 규칙 밖 이름의 폴더는 대상이 아니다 — 잡이 `reason=unknown_name` 줄로 남긴다(리뷰 권고).
"""
import re
from collections import namedtuple

from pipeline.gold import control as gold_control
from pipeline.silver import control as silver_control

_GENERATION = re.compile(r"g([1-9][0-9]*)")
_VERSION = re.compile(r"v([1-9][0-9]*)")
_TABLE = re.compile(r"[a-z][a-z0-9_]*")
Settings = namedtuple("Settings", "domain root s3")
Folders = namedtuple("Folders", "numbers unknown")  # 규칙에 맞는 번호(오름차순)·이름, 규칙 밖 이름


def job_settings(environ, domain):
    """정리 잡 설정 — 없거나 비면 ValueError. 반환 Settings(domain, root, s3 — control.s3_settings 결과 또는 None)."""
    silver_control._domain(domain)
    root = environ.get("VACUUM_LAKE_ROOT", "")
    if not root.strip():
        raise ValueError("정리 잡 설정이 없다(기본값 없음): VACUUM_LAKE_ROOT")
    silver_control._lake_path("VACUUM_LAKE_ROOT", root)
    return Settings(domain, root, silver_control.s3_settings(environ, (root,)))


def _parent(path):
    return path.rsplit("/", 1)[0]


def _numbers(names, pattern):
    numbers, unknown = set(), []
    for name in names:
        found = pattern.fullmatch(name)
        if found:
            numbers.add(int(found.group(1)))
        else:
            unknown.append(name)
    return Folders(sorted(numbers), sorted(unknown))


def generations(names):
    """세대 부모 폴더 아래 이름 → Folders(세대 번호, 규칙 밖 이름). `g1`·`g12` 는 받고 `g0`·`g01`·`gx` 는 규칙 밖."""
    return _numbers(names, _GENERATION)


def table_versions(names):
    """Gold 표 폴더 아래 이름 → Folders(표 판 번호, 규칙 밖 이름)."""
    return _numbers(names, _VERSION)


def gold_tables(root, domain, names):
    """원천 Gold 폴더 아래 이름 → Folders(표 이름, 규칙 밖 이름). 정정 기록 폴더는 표가 아니라 targets 가 따로 넣는다(둘 다에 들지 않음)."""
    corrections = gold_control.corrections_path(root, domain).rsplit("/", 1)[1]
    names = [name for name in names if name != corrections]
    return Folders(sorted(name for name in names if _TABLE.fullmatch(name)), sorted(name for name in names if not _TABLE.fullmatch(name)))


def silver_parents(root, domain):
    """세대 폴더의 부모 넷 — silver_paths 의 Silver 표 넷(checkpoint 빼고)에서 마지막 조각을 뗀 자리."""
    return [_parent(path) for path in silver_control.silver_paths(root, domain, 1)[:4]]


def gold_dir(root, domain):
    """원천 Gold 폴더 — 정정 기록 자리의 부모."""
    return _parent(gold_control.corrections_path(root, domain))


def table_dir(root, domain, name):
    """Gold 표 하나의 폴더(판 v<N> 들의 부모)."""
    return _parent(gold_control.table_path(root, domain, name, 1))


def targets(root, domain, generation_numbers, gold):
    """정리할 표 경로(순서: 세대마다 Silver 넷, 포인터, Gold 표·판, 정정). `gold`: {표 이름: [판 번호]}."""
    paths = [path for n in generation_numbers for path in silver_control.silver_paths(root, domain, n)[:4]]
    paths.append(silver_control.pointer_path(root, domain))
    paths += [gold_control.table_path(root, domain, name, v) for name in sorted(gold) for v in gold[name]]
    paths.append(gold_control.corrections_path(root, domain))
    return list(dict.fromkeys(paths))
