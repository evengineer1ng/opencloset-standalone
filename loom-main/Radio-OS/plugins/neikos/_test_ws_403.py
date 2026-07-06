"""Test WebSocket 403 diagnosis."""
import asyncio
import websockets

async def test():
    # Test 1: no origin header
    try:
        async with websockets.connect('ws://127.0.0.1:7700/ws/puck') as ws:
            print('Connected (no origin)!')
            await ws.send('{"type": "ping"}')
            resp = await asyncio.wait_for(ws.recv(), timeout=3)
            print('Got:', resp)
    except Exception as e:
        print('No-origin error:', type(e).__name__, str(e)[:200])

    # Test 2: with origin header
    try:
        async with websockets.connect(
            'ws://127.0.0.1:7700/ws/puck',
            additional_headers={'Origin': 'http://localhost:7700'}
        ) as ws:
            print('Connected (with origin)!')
            await ws.send('{"type": "ping"}')
            resp = await asyncio.wait_for(ws.recv(), timeout=3)
            print('Got:', resp)
    except Exception as e:
        print('With-origin error:', type(e).__name__, str(e)[:200])

asyncio.run(test())
