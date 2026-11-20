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
LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY", "") or os.getenv("LANGCHAIN_API_KEY", "")
LANGSMITH_PROJECT = env("LANGSMITH_PROJECT", "kai-bot")
LANGCHAIN_TRACING_V2 = env("LANGCHAIN_TRACING_V2", "false").lower() in {"1", "true", "yes", "on"}


def langsmith_enabled() -> bool:
    return bool(LANGSMITH_API_KEY) and LANGCHAIN_TRACING_V2


def apply_langsmith_env() -> None:
    """Point LangGraph / LangChain at LangSmith when the key is present."""
    if not langsmith_enabled():
        return
    # Force — setdefault would leave a stale false from an earlier process env.
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = LANGSMITH_API_KEY
    os.environ["LANGSMITH_API_KEY"] = LANGSMITH_API_KEY
    os.environ["LANGCHAIN_PROJECT"] = LANGSMITH_PROJECT
    os.environ["LANGSMITH_PROJECT"] = LANGSMITH_PROJECT
