#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Remote Message Relay Server — deploy to Ubuntu public server.

Run: REMOTE_TOKEN=<your-32-char-token> python server.py
  Deps: pip install aiohttp
"""

import os
import json
import time
import asyncio
import logging
from collections import defaultdict

from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("relay")

TOKEN = os.environ.get("REMOTE_TOKEN", "change-me-to-a-random-32-char-string")
PORT = int(os.environ.get("PORT", "8765"))
MAX_MSG_LEN = 500
RATE_LIMIT = 30
RATE_WINDOW = 60

connected_clients = set()
rate_buckets = defaultdict(list)


def check_rate(ip: str) -> bool:
    now = time.time()
    bucket = rate_buckets[ip]
    bucket[:] = [t for t in bucket if now - t < RATE_WINDOW]
    if len(bucket) >= RATE_LIMIT:
        return False
    bucket.append(now)
    return True


async def handle_api_send(request: web.Request):
    if request.query.get("token", "") != TOKEN:
        logger.warning("API: bad token from %s", request.remote)
        return web.json_response({"error": "unauthorized"}, status=401)

    ip = request.remote
    if not check_rate(ip):
        logger.warning("API: rate limit hit from %s", ip)
        return web.json_response({"error": "rate limit exceeded"}, status=429)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    text = body.get("text", "").strip()
    if not text:
        return web.json_response({"error": "text is required"}, status=400)
    if len(text) > MAX_MSG_LEN:
        return web.json_response({"error": "text too long"}, status=400)

    logger.info("API: message from %s: %s", ip, text[:80])
    payload = json.dumps({"text": text})

    disconnected = set()
    for ws in connected_clients.copy():
        try:
            await ws.send_str(payload)
        except (ConnectionResetError, ConnectionError):
            disconnected.add(ws)
    connected_clients.difference_update(disconnected)

    return web.json_response({"status": "ok"})


async def handle_ws(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    if request.query.get("token") != TOKEN:
        logger.warning("WS: bad token from %s", request.remote)
        await ws.close(code=4001, message="unauthorized")
        return ws

    connected_clients.add(ws)
    logger.info("WS: client connected from %s (total: %d)", request.remote, len(connected_clients))
    try:
        async for msg in ws:
            pass
    except (ConnectionResetError, ConnectionError):
        pass
    finally:
        connected_clients.discard(ws)
        logger.info("WS: client disconnected from %s (total: %d)", request.remote, len(connected_clients))
    return ws


async def main():
    app = web.Application()
    app.router.add_post("/api/send", handle_api_send)
    app.router.add_get("/ws", handle_ws)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Relay server listening on port %d", PORT)

    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
