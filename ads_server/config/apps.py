from django.apps import AppConfig


class AssemblyConfig(AppConfig):
    """조립 층(config — 층 규칙 밖, tests/dev/boundaries/test_layers.py). 여러 모듈의 설정을 함께 보는 시스템 검사를 등록한다."""

    name = "config"
    label = "config"  # 고정(설계 R3)

    def ready(self):
        from . import checks  # noqa: F401  (@checks.register)
