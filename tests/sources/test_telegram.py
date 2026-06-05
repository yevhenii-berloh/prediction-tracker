from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.errors import (
    ChannelInvalidError,
    ChannelPrivateError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)

from prophet_checker.models.domain import PersonSource, SourceType
from prophet_checker.sources.telegram import TelegramSource


def make_message(msg_id: int, text: str | None, date: datetime):
    m = MagicMock()
    m.id = msg_id
    m.text = text
    m.date = date
    return m


def make_mock_client(messages, get_entity_raises=None):
    client = MagicMock()
    if get_entity_raises is not None:
        client.get_entity = AsyncMock(side_effect=get_entity_raises("test"))
    else:
        client.get_entity = AsyncMock(return_value=MagicMock())

    captured_kwargs: dict = {}

    def iter_messages_factory(entity, **kwargs):
        captured_kwargs.update(kwargs)

        async def gen():
            for m in messages:
                yield m

        return gen()

    client.iter_messages = iter_messages_factory
    client._iter_kwargs = captured_kwargs
    return client


def make_person_source(
    channel: str = "O_Arestovich_official",
    source_type: SourceType = SourceType.TELEGRAM,
) -> PersonSource:
    return PersonSource(
        id="ps_test",
        person_id="person_test",
        source_type=source_type,
        source_identifier=channel,
        enabled=True,
    )


@pytest.mark.asyncio
async def test_collect_yields_filtered_documents():
    long_text = "А" * 100
    short_text = "shrt"
    msgs = [
        make_message(1, long_text, datetime(2024, 6, 1, tzinfo=UTC)),
        make_message(2, short_text, datetime(2024, 6, 2, tzinfo=UTC)),
        make_message(3, None, datetime(2024, 6, 3, tzinfo=UTC)),
    ]
    client = make_mock_client(msgs)
    source = TelegramSource(client)

    yielded = []
    async for doc in source.collect(make_person_source()):
        yielded.append(doc)

    assert len(yielded) == 1
    assert yielded[0].raw_text == long_text
    assert yielded[0].source_type == SourceType.TELEGRAM
    assert yielded[0].person_id == "person_test"
    assert yielded[0].language == "uk"


@pytest.mark.asyncio
async def test_collect_builds_telegram_url():
    long_text = "А" * 100
    msgs = [make_message(42, long_text, datetime(2024, 6, 1, tzinfo=UTC))]
    client = make_mock_client(msgs)
    source = TelegramSource(client)
    ps = make_person_source(channel="O_Arestovich_official")

    yielded = []
    async for doc in source.collect(ps):
        yielded.append(doc)

    assert yielded[0].url == "https://t.me/O_Arestovich_official/42"


@pytest.mark.asyncio
async def test_collect_preserves_published_at():
    long_text = "А" * 100
    msg_date = datetime(2024, 7, 15, 12, 30, tzinfo=UTC)
    msgs = [make_message(1, long_text, msg_date)]
    client = make_mock_client(msgs)
    source = TelegramSource(client)

    yielded = []
    async for doc in source.collect(make_person_source()):
        yielded.append(doc)

    assert yielded[0].published_at == msg_date


@pytest.mark.asyncio
async def test_collect_passes_reverse_and_offset_date_to_iter_messages():
    long_text = "А" * 100
    msgs = [
        make_message(1, long_text, datetime(2024, 7, 1, tzinfo=UTC)),
        make_message(2, long_text, datetime(2024, 8, 1, tzinfo=UTC)),
    ]
    client = make_mock_client(msgs)
    source = TelegramSource(client)
    since = datetime(2024, 6, 1, tzinfo=UTC)

    yielded = []
    async for doc in source.collect(make_person_source(), since=since):
        yielded.append(doc)

    assert client._iter_kwargs == {"reverse": True, "offset_date": since}
    assert len(yielded) == 2
    assert yielded[0].published_at == datetime(2024, 7, 1, tzinfo=UTC)
    assert yielded[1].published_at == datetime(2024, 8, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_collect_passes_offset_date_none_when_since_not_set():
    long_text = "А" * 100
    msgs = [make_message(1, long_text, datetime(2024, 8, 1, tzinfo=UTC))]
    client = make_mock_client(msgs)
    source = TelegramSource(client)

    async for _ in source.collect(make_person_source()):
        pass

    assert client._iter_kwargs == {"reverse": True, "offset_date": None}


@pytest.mark.asyncio
async def test_collect_skips_non_telegram_source():
    msgs = [make_message(1, "А" * 100, datetime(2024, 6, 1, tzinfo=UTC))]
    client = make_mock_client(msgs)
    source = TelegramSource(client)
    ps = make_person_source(source_type=SourceType.NEWS)

    yielded = []
    async for doc in source.collect(ps):
        yielded.append(doc)

    assert yielded == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error_class", [
    ChannelInvalidError,
    ChannelPrivateError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
    ValueError,
])
async def test_collect_propagates_channel_access_error(error_class):
    client = make_mock_client([], get_entity_raises=error_class)
    source = TelegramSource(client)

    with pytest.raises(error_class):
        async for _ in source.collect(make_person_source()):
            pass


@pytest.mark.asyncio
async def test_collect_since_none_yields_all():
    long_text = "А" * 100
    msgs = [
        make_message(2, long_text, datetime(2024, 8, 1, tzinfo=UTC)),
        make_message(1, long_text, datetime(2020, 1, 1, tzinfo=UTC)),
    ]
    client = make_mock_client(msgs)
    source = TelegramSource(client)

    yielded = []
    async for doc in source.collect(make_person_source(), since=None):
        yielded.append(doc)

    assert len(yielded) == 2


@pytest.mark.asyncio
async def test_collect_maps_epoch_since_to_none_offset_date():
    long_text = "А" * 100
    msgs = [make_message(1, long_text, datetime(2024, 8, 1, tzinfo=UTC))]
    client = make_mock_client(msgs)
    source = TelegramSource(client)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)

    async for _ in source.collect(make_person_source(), since=epoch):
        pass

    assert client._iter_kwargs == {"reverse": True, "offset_date": None}
