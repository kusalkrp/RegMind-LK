"""
Unit tests for config.py and database.py.
No external connections required — all settings loaded from environment.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError


# ─── Config tests ─────────────────────────────────────────────────────────────

class TestSettings:
    def test_loads_required_settings_from_env(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://user:pass@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
        monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")

        # Clear lru_cache so monkeypatched env is picked up
        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        settings = get_settings()
        assert settings.postgres_url == "postgresql+asyncpg://user:pass@localhost/db"
        assert settings.gemini_api_key == "test-key-123"
        assert settings.qdrant_url == "http://localhost:6333"

    def test_defaults_are_applied(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://user:pass@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")

        from sltda_mcp.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()

        assert settings.mcp_max_concurrency == 15
        assert settings.rag_top_k_chunks == 6
        assert settings.rag_similarity_threshold == 0.60
        assert settings.log_level == "INFO"
        assert settings.ingestion_rate_limit_rps == 1.0
        assert settings.ingestion_min_file_size_kb == 5

    def test_log_level_uppercased(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("LOG_LEVEL", "debug")

        from sltda_mcp.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()

        assert settings.log_level == "DEBUG"

    def test_invalid_log_level_raises(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("LOG_LEVEL", "VERBOSE")

        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        with pytest.raises(ValidationError, match="log_level"):
            get_settings()

    def test_missing_required_fields_raises(self, monkeypatch):
        # Remove all env vars to ensure required fields fail
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        with pytest.raises(ValidationError):
            get_settings()

    def test_qdrant_collection_names(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")

        from sltda_mcp.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()

        assert settings.qdrant_collection == "sltda_documents"
        assert settings.qdrant_staging_collection == "sltda_documents_next"
        assert settings.qdrant_exemplars_collection == "format_exemplars"

    def test_get_settings_is_cached(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("GEMINI_API_KEY", "key")

        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2  # same object — lru_cache working

    def teardown_method(self):
        from sltda_mcp.config import get_settings
        get_settings.cache_clear()


# ─── Secrets must not appear in source code ──────────────────────────────────

class TestNoSecretsInSource:
    def test_gemini_api_key_not_hardcoded(self):
        import pathlib
        src = pathlib.Path("src")
        for py_file in src.rglob("*.py"):
            content = py_file.read_text()
            assert "AIzaSy" not in content, f"Possible API key in {py_file}"
            assert "sk-" not in content, f"Possible secret key in {py_file}"

    def test_postgres_password_not_hardcoded(self):
        import pathlib
        src = pathlib.Path("src")
        for py_file in src.rglob("*.py"):
            content = py_file.read_text()
            # No hardcoded connection strings with passwords
            assert "postgresql://sltda:changeme" not in content, (
                f"Hardcoded DB URL in {py_file}"
            )


# ─── Database pool tests ──────────────────────────────────────────────────────

class TestDatabasePool:
    @pytest.mark.asyncio
    async def test_get_pool_raises_before_init(self):
        from sltda_mcp.database import get_pool
        import sltda_mcp.database as db_module

        original_pool = db_module._pool
        db_module._pool = None
        try:
            with pytest.raises(RuntimeError, match="not initialised"):
                get_pool()
        finally:
            db_module._pool = original_pool

    @pytest.mark.asyncio
    async def test_pool_stats_returns_dict(self):
        from sltda_mcp.database import pool_stats
        import sltda_mcp.database as db_module

        mock_pool = MagicMock()
        mock_pool.get_size.return_value = 15
        mock_pool.get_idle_size.return_value = 10

        db_module._pool = mock_pool
        try:
            stats = await pool_stats()
            assert stats["pool_size"] == 15
            assert stats["pool_free"] == 10
            assert stats["pool_used"] == 5
        finally:
            db_module._pool = None

    @pytest.mark.asyncio
    async def test_init_pool_converts_url(self, monkeypatch):
        """Verifies asyncpg receives postgresql:// not postgresql+asyncpg://"""
        import sltda_mcp.database as db_module

        captured_dsn = {}

        async def mock_create_pool(*, dsn, **kwargs):
            captured_dsn["dsn"] = dsn
            mock_pool = AsyncMock()
            mock_pool.get_size.return_value = 5
            mock_pool.get_idle_size.return_value = 5
            return mock_pool

        monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://user:pass@localhost/testdb")
        monkeypatch.setenv("GEMINI_API_KEY", "key")

        from sltda_mcp.config import get_settings
        get_settings.cache_clear()

        with patch("asyncpg.create_pool", side_effect=mock_create_pool):
            db_module._pool = None
            await db_module.init_pool()
            assert "postgresql+asyncpg" not in captured_dsn["dsn"]
            assert captured_dsn["dsn"].startswith("postgresql://")
            await db_module.close_pool()

        get_settings.cache_clear()
