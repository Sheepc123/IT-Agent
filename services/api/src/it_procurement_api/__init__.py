import uvicorn

from .settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "it_procurement_api.app:app",
        host=settings.api_host,
        port=settings.api_port,
    )
