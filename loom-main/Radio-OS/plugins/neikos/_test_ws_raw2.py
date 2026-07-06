"""Test WebSocket 403 diagnosis - get response body."""
import asyncio
import websockets
import websockets.http11
import socket

async def test_raw():
    # Raw HTTP upgrade to see what server says
    reader, writer = await asyncio.open_connection('127.0.0.1', 7700)
    request = (
        b"GET /ws/puck HTTP/1.1\r\n"
        b"Host: 127.0.0.1:7700\r\n"
        b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n"
        b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        b"Sec-WebSocket-Version: 13\r\n"
        b"\r\n"
    )
    writer.write(request)
    await writer.drain()
    response = await reader.read(4096)
    writer.close()
    print("Raw HTTP response:")
    print(response.decode('utf-8', errors='replace'))

asyncio.run(test_raw())
