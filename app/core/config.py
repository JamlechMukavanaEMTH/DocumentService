"""Application configuration (Pydantic Settings)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://docuser:docpass@localhost:5432/docservice"
    )

    # Redis (ARQ)
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    # Storage
    STORAGE_BACKEND: str = Field(default="local")
    STORAGE_ROOT: str = Field(default="/app/data/storage")
    STORAGE_BUCKET: str | None = Field(default=None)
    MAX_FILE_SIZE_BYTES: int = Field(default=104857600)

    # OCR
    OCR_LANGUAGE: str = Field(default="eng")

    # PDF optimization (pikepdf/qpdf)
    ENABLE_PDF_OPTIMIZE: bool = Field(default=True)

    # Compression
    DEFAULT_COMPRESSION_LEVEL: int = Field(default=6)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
