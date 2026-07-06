"""Minimal ws test using a raw HTTP/1.1 upgrade with proper handshake."""
import asyncio
import base64
import hashlib
import socket


def ws_handshake_test():
    """Pure TCP WebSocket handshake test - no external library."""
    key = base64.b64encode(b"neikos_test_1234").decode()  # 16 bytes base64
    
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(('127.0.0.1', 7700))
    
    request = (
        f"GET /ws/puck HTTP/1.1\r\n"
        f"Host: 127.0.0.1:7700\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    ).encode()
    
    s.send(request)
    response = s.recv(4096)
    print("Server response:")
    print(response.decode('utf-8', errors='replace'))
    
    if b"101 Switching Protocols" in response:
        print("[PASS] WebSocket handshake succeeded!")
        
        # Send a ping frame (opcode 0x9, fin=1, mask=1)
        import os
        mask = os.urandom(4)
        payload = b'{"type":"ping"}'
        masked = bytes([b ^ mask[i % 4] for i, b in enumerate(payload)])
        frame = bytes([0x81, 0x80 | len(payload)]) + mask + masked  # 0x81 = text frame
        s.send(frame)
        
        # Read response
        response2 = s.recv(4096)
        print("WS response:", response2[:100])
    else:
        print("[FAIL] No 101 response")
    
    s.close()


ws_handshake_test()
