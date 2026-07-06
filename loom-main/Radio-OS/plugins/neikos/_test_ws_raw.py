"""Test WebSocket by doing a manual HTTP upgrade request to see what error we get."""
import socket
import base64
import os

host = "127.0.0.1"
port = 7700
path = "/ws/puck"

# Generate a valid Sec-WebSocket-Key
key = base64.b64encode(os.urandom(16)).decode()

request = (
    f"GET {path} HTTP/1.1\r\n"
    f"Host: {host}:{port}\r\n"
    f"Upgrade: websocket\r\n"
    f"Connection: Upgrade\r\n"
    f"Sec-WebSocket-Key: {key}\r\n"
    f"Sec-WebSocket-Version: 13\r\n"
    f"Origin: http://localhost:7700\r\n"
    f"\r\n"
)

s = socket.socket()
s.connect((host, port))
s.send(request.encode())
response = b""
while True:
    chunk = s.recv(4096)
    if not chunk:
        break
    response += chunk
    if b"\r\n\r\n" in response:
        # Got headers
        header_end = response.index(b"\r\n\r\n")
        print("=== RAW RESPONSE ===")
        print(response[:header_end + 4].decode(errors="replace"))
        print("=== END RESPONSE ===")
        break
s.close()
