from pathlib import Path
import unittest
from sqlalchemy.engine import make_url
from app.config import Settings


class ConfigPathTests(unittest.TestCase):
    def test_relative_database_is_always_under_backend(self):
        settings = Settings(DATABASE_URL="sqlite+aiosqlite:///./drowsyguard.db",_env_file=None)
        expected = Path(__file__).resolve().parents[1] / "drowsyguard.db"
        self.assertEqual(Path(make_url(settings.DATABASE_URL).database),expected)

    def test_memory_and_remote_urls_are_not_rewritten(self):
        for url in ("sqlite+aiosqlite:///:memory:","postgresql+asyncpg://example/db"):
            self.assertEqual(Settings(DATABASE_URL=url,_env_file=None).DATABASE_URL,url)
