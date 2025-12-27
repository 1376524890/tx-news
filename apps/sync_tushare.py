from __future__ import annotations

from tx_news.logging import configure_logging
from tx_news.tasks.maintenance import sync_tushare


def main() -> None:
    configure_logging()
    # run synchronously via Celery task function
    res = sync_tushare.apply().get()
    print(res)


if __name__ == "__main__":
    main()

