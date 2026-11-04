import os


def env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value else default


DATABASE_URL = env("DATABASE_URL", "postgresql+psycopg://kai:kai@localhost:5432/kai")
TEMPORAL_ADDRESS = env("TEMPORAL_ADDRESS", "localhost:7233")
TEMPORAL_NAMESPACE = env("TEMPORAL_NAMESPACE", "kai")
REDIS_URL = env("REDIS_URL", "redis://localhost:6379/0")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-3.8-flash")
TASK_QUEUE = env("TASK_QUEUE", "kai-main")
