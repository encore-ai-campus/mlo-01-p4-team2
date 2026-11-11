from django.apps import AppConfig


class InternalApiConfig(AppConfig):
    name = "internal_api"
    label = "internal_api"  # 고정(설계 R3). 모델이 없다: 데이터는 아래 층(campaigns·masters)이 소유한다
