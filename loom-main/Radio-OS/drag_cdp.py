import websocket
import json
import time

ws = websocket.WebSocket()
ws.connect('ws://localhost:9222/devtools/page/03912DF70AC963934AB5B769EB996BC5', suppress_origin=True)

msg_id = [1]

def send(method, params={}):
    id_ = msg_id[0]
    msg_id[0] += 1
    ws.send(json.dumps({'id': id_, 'method': method, 'params': params}))
    while True:
        resp = json.loads(ws.recv())
        if resp.get('id') == id_:
            return resp

sx, sy = 1192, 198
dx, dy = 768, 371

send('Input.dispatchMouseEvent', {'type':'mousePressed','x':sx,'y':sy,'button':'left','buttons':1,'clickCount':1})
time.sleep(0.05)
steps = 20
for i in range(1, steps+1):
    nx = int(sx + (dx-sx)*i/steps)
    ny = int(sy + (dy-sy)*i/steps)
    send('Input.dispatchMouseEvent', {'type':'mouseMoved','x':nx,'y':ny,'button':'left','buttons':1})
    time.sleep(0.03)
send('Input.dispatchMouseEvent', {'type':'mouseReleased','x':dx,'y':dy,'button':'left','buttons':0,'clickCount':1})
time.sleep(0.5)
print('done')
ws.close()
