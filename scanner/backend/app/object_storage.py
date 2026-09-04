"""Облачное хранилище файлов (S3): чертежи и съёмка живут в бакете.

Архитектурно (ADR-0005): PostgreSQL — записи, объектное хранилище — байты.
Приёмный буфер процесса принимает чанки и сверяет контрольную сумму, а
подтверждённый файл уезжает в бакет; ключ детерминирован —
`{tenant}/{session}/{asset_id}` — и восстановим из любой записи о сессии.

Конфигурация — из окружения (KVANT_S3_*); локально переменные подтягиваются
из scanner/backend/.env. Без конфигурации хранилище честно отсутствует
(None), файлы остаются в приёмном буфере — поведение дев-стенда без облака
не меняется.
"""

from __future__ import annotations

import os
import pathlib
from typing import Any, Protocol


class ObjectStorage(Protocol):
    def put(self, key: str, data: bytes, *, mime: str,
            metadata: dict[str, str]) -> None: ...

    def get(self, key: str) -> bytes | None: ...


class S3ObjectStorage:
    """Бакет S3-совместимого облака (Timeweb, Яндекс, MinIO — один код).

    Клиент ленивый: boto3 импортируется при первом обращении, чтобы тесты
    без облака не платили за импорт SDK.
    """

    def __init__(self, *, endpoint: str, region: str, bucket: str,
                 access_key: str, secret_key: str) -> None:
        self._endpoint = endpoint
        self._region = region
        self.bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key
        self._client: Any = None

    def _s3(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=self._endpoint,
                region_name=self._region,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
                config=Config(s3={"addressing_style": "path"},
                              retries={"max_attempts": 2}),
            )
        return self._client

    def put(self, key: str, data: bytes, *, mime: str,
            metadata: dict[str, str]) -> None:
        self._s3().put_object(Bucket=self.bucket, Key=key, Body=data,
                              ContentType=mime, Metadata=metadata)

    def get(self, key: str) -> bytes | None:
        try:
            return self._s3().get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self._s3().exceptions.NoSuchKey:
            return None


def _load_dotenv_once() -> None:
    """ТОЛЬКО KVANT_S3_* из .env рядом с бэкендом, только недостающие ключи.

    Не весь файл намеренно: подтянув .env целиком, этот модуль однажды
    занёс в окружение ANTHROPIC_API_KEY — и каждый тест с завершённой
    сессией пошёл в живую модель за реальные деньги. Чужие секреты — не
    забота хранилища файлов; реальное окружение (прод, CI) сильнее файла.
    """
    env_file = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            if key.strip().startswith("KVANT_S3_"):
                os.environ.setdefault(key.strip(), value.strip())


def object_storage_from_env() -> S3ObjectStorage | None:
    _load_dotenv_once()
    required = ("KVANT_S3_ENDPOINT", "KVANT_S3_BUCKET",
                "KVANT_S3_ACCESS_KEY", "KVANT_S3_SECRET_KEY")
    if not all(os.environ.get(k) for k in required):
        return None
    return S3ObjectStorage(
        endpoint=os.environ["KVANT_S3_ENDPOINT"],
        region=os.environ.get("KVANT_S3_REGION", "ru-1"),
        bucket=os.environ["KVANT_S3_BUCKET"],
        access_key=os.environ["KVANT_S3_ACCESS_KEY"],
        secret_key=os.environ["KVANT_S3_SECRET_KEY"],
    )
