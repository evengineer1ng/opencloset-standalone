from __future__ import annotations
import asyncio
import json
import threading
import logging
from typing import Dict, Optional, Any

log = logging.getLogger("nk.puck")


class PuckManager:
    """
    Manages ESP32 puck connections. Each puck registers with:
      { "type": "register", "node_id": "N001", "puck_id": "puck-a3f2" }

    When player moves to a node, the puck at that node gets:
      { "type": "activate", "node_type": "WILD_ZONE", "tier": 1, "is_relay": false }

    Other pucks get:
      { "type": "ambient", "node_type": "...", "tier": 1 }

    Button press from puck:
      { "type": "interact", "node_id": "N001", "puck_id": "puck-a3f2" }
    → triggers explore action at that node if player is there
    """

    def __init__(self, controller):
        self._controller = controller
        self._pucks: Dict[str, Any] = {}       # puck_id → websocket
        self._puck_nodes: Dict[str, str] = {}  # puck_id → node_id
        self._node_pucks: Dict[str, str] = {}  # node_id → puck_id
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set the asyncio event loop used for cross-thread WebSocket sends.
        Call this from inside an async handler (e.g. the WebSocket handler or
        an app startup event) so we have the running loop reference."""
        self._loop = loop

    def register(self, puck_id: str, node_id: str, ws):
        with self._lock:
            self._pucks[puck_id] = ws
            self._puck_nodes[puck_id] = node_id
            self._node_pucks[node_id] = puck_id
        log.info(f"Puck {puck_id} registered at node {node_id}")
        self._send(puck_id, {"type": "registered", "node_id": node_id})

    def unregister(self, puck_id: str):
        with self._lock:
            node = self._puck_nodes.pop(puck_id, None)
            if node:
                self._node_pucks.pop(node, None)
            self._pucks.pop(puck_id, None)
        log.info(f"Puck {puck_id} disconnected")

    def on_player_move(self, new_node_id: str, node_type: str, tier: int, is_relay: bool):
        """Called when player moves. Activate the target puck, ambient all others."""
        with self._lock:
            for pid, nid in self._puck_nodes.items():
                if nid == new_node_id:
                    self._send(pid, {
                        "type": "activate",
                        "node_type": node_type,
                        "tier": tier,
                        "is_relay": is_relay,
                    })
                else:
                    self._send(pid, {
                        "type": "ambient",
                        "node_type": node_type,
                        "tier": tier,
                    })

    def on_interact(self, puck_id: str):
        """Button press on a puck — trigger explore at that node if player is there."""
        node_id = self._puck_nodes.get(puck_id)
        if not node_id:
            return
        st = self._controller._state
        if st and st.player_location == node_id:
            self._controller._cmd_q.put({"action": "explore"})

    def broadcast(self, msg: dict):
        with self._lock:
            for pid in list(self._pucks.keys()):
                self._send(pid, msg)

    def connected_count(self) -> int:
        return len(self._pucks)

    def status(self) -> dict:
        """Return a status dict for /api/puck/status."""
        with self._lock:
            return {
                "connected": len(self._pucks),
                "loop_ready": self._loop is not None,
                "pucks": [
                    {"puck_id": pid, "node_id": self._puck_nodes.get(pid)}
                    for pid in self._pucks
                ],
            }

    def _send(self, puck_id: str, msg: dict):
        """Send a JSON message to a puck WebSocket.
        
        This may be called from the controller worker thread (synchronous context),
        so we schedule the coroutine on the stored event loop via
        run_coroutine_threadsafe instead of awaiting directly.
        """
        ws = self._pucks.get(puck_id)
        if ws is None:
            return
        if self._loop is None or not self._loop.is_running():
            # Loop not available — log and skip; message dropped gracefully
            log.debug(f"Puck send skipped (no loop) {puck_id}: {msg.get('type')}")
            return
        try:
            asyncio.run_coroutine_threadsafe(ws.send_json(msg), self._loop)
        except Exception as e:
            log.warning(f"Puck send failed {puck_id}: {e}")


_puck_manager: Optional[PuckManager] = None


def get_puck_manager() -> Optional[PuckManager]:
    return _puck_manager


def init_puck_manager(controller) -> PuckManager:
    global _puck_manager
    _puck_manager = PuckManager(controller)
    return _puck_manager
