"""
HuggingFace Transformers LMTP inference server implementation.

To run an instance of this LMTP server, run e.g. the following command:

lmql serve-model --cuda gpt2-medium

See lmtp_client for an example how to connect to this server via 'websocket'.
"""

import asyncio
import json
import aiohttp
import warnings
from aiohttp import web

from .lmtp_scheduler import *

class LMTPWebSocketTransport:
    """
    Exposes a LMTP TokenSession over a websocket.
    """
    def __init__(self, ws):
        self.ws = ws
        self.queue = asyncio.Queue()
        self._dumper = asyncio.create_task(self.dumper())

    async def dumper(self):
        while True:
            try:
                type, payload = await self.queue.get()
                await self.ws.send_str(f"{type} {json.dumps([payload])}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                warnings.warn("LMTPWebSocketTransport.dumper error".format(e))

    async def send(self, type, payload):
        await self.queue.put((type, payload))

    @staticmethod
    async def listen(ws, model_args, static):
        transport = LMTPWebSocketTransport(ws)
        session = TokenSession(transport, model_args, static=static, longrunning=True)

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    cmd, kwargs = msg.data.split(" ", 1)
                    
                    kwargs = json.loads(kwargs)
                    logit_bias = kwargs.pop("logit_bias", {})
                    logit_bias = {int(k): float(v) for k, v in logit_bias.items()}
                    kwargs["logit_bias"] = logit_bias
                    
                    await session.handle(cmd, kwargs)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
        finally:
            transport._dumper.cancel()
            session.close()
            await ws.close()

    async def close(self):
        await self.ws.close()
