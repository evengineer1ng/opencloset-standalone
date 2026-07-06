import asyncio
import sys

async def test():
    try:
        import websockets
        print('websockets version:', websockets.__version__)
        ws = await asyncio.wait_for(
            websockets.connect(
                'ws://127.0.0.1:7700/ws/puck',
                additional_headers={'Origin': 'http://localhost:7700'}
            ),
            timeout=5
        )
        print('Connected OK!')
        import json
        await ws.send(json.dumps({"type": "ping"}))
        resp = await asyncio.wait_for(ws.recv(), timeout=3)
        print('Response:', resp)
        await ws.close()
        print('Closed OK')
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(test())
