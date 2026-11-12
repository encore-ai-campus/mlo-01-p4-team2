"""Coalesce due checkpoints without changing their per-socket save/cleanup contract."""
import asyncio
from collections import deque

from . import socket_db
from .positions import POSITION_BATCH_SIZE, save_positions


def store_positions_now(requests):
    users = [consumer.session_user() for consumer, _, _ in requests]
    rows = [(user, state, observed_at, consumer.platform_session_uuid)
            for (consumer, state, observed_at), user in zip(requests, users) if user is not None]
    due = save_positions(rows)
    return [due.get(user.pk) if user is not None else None for user in users]


class PositionBatch:
    def __init__(self):
        self.pending = deque()
        self.task = None

    async def save(self, consumer, state, observed_at):
        future = asyncio.get_running_loop().create_future()
        # A disconnected waiter must not leave an unobserved exception or cancel another socket's save.
        future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        self.pending.append(((consumer, state, observed_at), future))
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.flush())
        return await asyncio.shield(future)

    async def flush(self):
        batch = []
        try:
            # Collect ready checkpoints; never wait for ten users or another five-second interval.
            await asyncio.sleep(0)
            while self.pending:
                batch = [self.pending.popleft() for _ in range(min(POSITION_BATCH_SIZE, len(self.pending)))]
                try:
                    results = await socket_db.run(store_positions_now, [row for row, _ in batch])
                except Exception as error:
                    for _, future in batch:
                        future.set_exception(error)
                else:
                    for (_, future), result in zip(batch, results):
                        future.set_result(result)
        finally:
            for _, future in [*batch, *self.pending]:
                if not future.done():
                    future.cancel()
            self.pending.clear()
            self.task = None
