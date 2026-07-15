"""Одноразовий інтерактивний логін у Telegram → свіжий tg_session.session у корені
репо. Потрібен, коли user-сесія Telethon померла (AuthKeyDuplicatedError) і збір
постів на проді впав. Читає TELEGRAM_API_ID / TELEGRAM_API_HASH з .env репо.

Інтерактивний: питає телефон → код із Telegram → 2FA-пароль (якщо є). Тому запуск
з термінала, НЕ через pipe/heredoc (інакше ввід коду нема звідки читати).

    uv run python deploy/make_session.py
    uv run python deploy/make_session.py --verify-channel @some_channel

Далі: ./deploy/secrets.sh -y put-file ./tg_session.session
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon.sync import TelegramClient

PROJECT_ROOT = Path(__file__).parent.parent
SESSION = PROJECT_ROOT / "tg_session"  # Telethon допише .session
DEFAULT_VERIFY_CHANNEL = "@O_Arestovich_official"


def move_dead_session_aside() -> None:
    dead = PROJECT_ROOT / "tg_session.session"
    if not dead.exists():
        return
    backup = PROJECT_ROOT / "tg_session.session.dead"
    dead.rename(backup)
    print(f"стару сесію відсунуто → {backup.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Створити свіжу Telethon user-сесію.")
    parser.add_argument(
        "--verify-channel",
        default=DEFAULT_VERIFY_CHANNEL,
        help="канал для перевірки, що сесія реально резолвить entity (default: %(default)s)",
    )
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]

    move_dead_session_aside()

    with TelegramClient(str(SESSION), api_id, api_hash) as client:
        me = client.get_me()
        print(f"✅ залогінено як @{me.username} (id={me.id})")

        entity = client.get_entity(args.verify_channel)
        print(f"✅ канал видно: {entity.title} ({args.verify_channel})")

    print(f"\nсесія готова: {SESSION}.session")
    print("далі: ./deploy/secrets.sh -y put-file ./tg_session.session")


if __name__ == "__main__":
    main()
