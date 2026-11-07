"""Keep per-connection cleanup outside handlers and their locks."""
import asyncio
from contextlib import suppress
import logging

from autobahn.exception import Disconnected
from channels.exceptions import StopConsumer
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class CleanupWebsocketConsumer(AsyncWebsocketConsumer):
    async def __call__(self, scope, receive, send):
        transport_closed = asyncio.Event()

        async def connected_send(message):
            try:
                await send(message)
            except (OSError, Disconnected):
                logger.info("WebSocket transport closed during send", exc_info=True)
                transport_closed.set()
                raise StopConsumer

        application = asyncio.create_task(super().__call__(scope, receive, connected_send))
        closed = asyncio.create_task(transport_closed.wait())
        try:
            await asyncio.wait((application, closed), return_when=asyncio.FIRST_COMPLETED)
            if not application.done():
                application.cancel()
                with suppress(asyncio.CancelledError):
                    await application
            else:
                await application
        finally:
            application.cancel()
            closed.cancel()
            await asyncio.gather(application, closed, return_exceptions=True)
            try:
                await self.disconnect(None)
            except Exception:
                logger.exception("WebSocket final cleanup failed")

    async def finish_task(self, task, *, cancel=True):
        if task is None:
            return
        if cancel:
            task.cancel()
        try:
            if cancel:
                await task
            else:
                await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.cancelled():
                raise
        except StopConsumer:
            pass
        except Exception:
            logger.exception("WebSocket task failed during cleanup")

    async def discard_groups(self):
        for group in list(getattr(self, "groups_joined", ())):
            try:
                await self.channel_layer.group_discard(group, self.channel_name)
            except Exception:
                logger.exception("Could not remove WebSocket from group %s", group)
            else:
                self.groups_joined.remove(group)
