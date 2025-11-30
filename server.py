# server.py
# Authoritative server: TCP lobby + UDP state updates
# Python 3.8+
import socket
import threading
import time
import json
import random
import struct
from collections import deque

HOST = '0.0.0.0'
TCP_PORT = 9000
UDP_PORT = 9001

TICK_RATE = 60                # server game logic ticks per second
BROADCAST_HZ = 20             # snapshots per second
SERVER_LATENCY = 0.1          # 100 ms server-side simulated delay

WORLD_W, WORLD_H = 800, 600
PLAYER_SPEED = 200.0          # px per second
PLAYER_RADIUS = 16
COIN_RADIUS = 10

MAX_PLAYERS = 2

def current_time():
    return time.time()

class Player:
    def __init__(self, pid):
        self.id = pid
        self.x = random.uniform(50, WORLD_W-50)
        self.y = random.uniform(50, WORLD_H-50)
        self.vx = 0.0
        self.vy = 0.0
        self.score = 0
        self.last_input_time = 0.0
        self.addr = None  # (ip, port) for UDP

class Server:
    def __init__(self):
        self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp_sock.bind((HOST, TCP_PORT))
        self.tcp_sock.listen(5)

        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_sock.bind((HOST, UDP_PORT))
        self.udp_sock.setblocking(False)

        self.players = {}  # pid -> Player
        self.next_pid = 1
        self.coins = []    # list of dicts {x,y,id}
        self.coin_next_id = 1

        self.lock = threading.Lock()
        self.running = True

        # Queues to simulate latency (process inbound after delay)
        self.inbound_queue = deque()   # (process_time, (data, addr))
        self.outbound_queue = deque()  # (send_time, (data, addr))

    def start(self):
        print("Server starting. TCP on", TCP_PORT, "UDP on", UDP_PORT)
        threading.Thread(target=self.tcp_accept_loop, daemon=True).start()
        threading.Thread(target=self.udp_recv_loop, daemon=True).start()
        threading.Thread(target=self.main_loop, daemon=True).start()
        threading.Thread(target=self.broadcaster_loop, daemon=True).start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Shutting down server")
            self.running = False

    # TCP: accept connections for lobby & handshake
    def tcp_accept_loop(self):
        while self.running:
            conn, addr = self.tcp_sock.accept()
            print("TCP connect from", addr)
            threading.Thread(target=self.handle_tcp_client, args=(conn,addr), daemon=True).start()

    def handle_tcp_client(self, conn, addr):
        with conn:
            # assign pid
            with self.lock:
                pid = self.next_pid
                self.next_pid += 1
                p = Player(pid)
                self.players[pid] = p
            # send assignment and UDP port
            payload = {"type":"welcome","pid":pid,"udp_port":UDP_PORT}
            conn.sendall(json.dumps(payload).encode())
            print(f"Assigned player {pid}")
            # simple blocking: wait until game start: server auto-start when MAX_PLAYERS connected
            while self.running:
                with self.lock:
                    if len(self.players) >= MAX_PLAYERS:
                        start_msg = {"type":"start","msg":"game starting"}
                        conn.sendall(json.dumps(start_msg).encode())
                        break
                time.sleep(0.1)
            # keep TCP alive until client disconnects
            try:
                while self.running:
                    time.sleep(1)
            except:
                pass
            with self.lock:
                if pid in self.players:
                    del self.players[pid]
                    print(f"Player {pid} disconnected (TCP)")

    # UDP: receive inputs, but simulate SERVER_LATENCY on processing
    def udp_recv_loop(self):
        while self.running:
            try:
                data, addr = self.udp_sock.recvfrom(4096)
                # enqueue for processing later
                process_time = current_time() + SERVER_LATENCY
                self.inbound_queue.append((process_time, (data, addr)))
            except BlockingIOError:
                pass
            # process inbound queue items whose time has arrived
            now = current_time()
            while self.inbound_queue and self.inbound_queue[0][0] <= now:
                _, (data, addr) = self.inbound_queue.popleft()
                try:
                    msg = json.loads(data.decode())
                except:
                    continue
                # Expect messages: {"type":"input","pid":..., "intent":{...}, "udp_port":...}
                if msg.get("type") == "input":
                    pid = msg.get("pid")
                    intent = msg.get("intent", {})
                    with self.lock:
                        p = self.players.get(pid)
                        if p:
                            # register UDP addr if not set
                            p.addr = addr
                            # apply as instantaneous velocity vector (server authoritative)
                            vx = 0.0; vy = 0.0
                            if intent.get("left"): vx -= 1
                            if intent.get("right"): vx += 1
                            if intent.get("up"): vy -= 1
                            if intent.get("down"): vy += 1
                            # normalize
                            mag = (vx*vx + vy*vy) ** 0.5
                            if mag > 0:
                                p.vx = (vx/mag) * PLAYER_SPEED
                                p.vy = (vy/mag) * PLAYER_SPEED
                            else:
                                p.vx = 0.0; p.vy = 0.0
                            p.last_input_time = current_time()
                # ignore other types

            # process outbound queue send times
            now = current_time()
            while self.outbound_queue and self.outbound_queue[0][0] <= now:
                _, (data, addr) = self.outbound_queue.popleft()
                try:
                    self.udp_sock.sendto(data, addr)
                except Exception as e:
                    # ignore send errors
                    pass
            time.sleep(0.001)

    # Main game loop: update physics and handle coin collisions
    def main_loop(self):
        prev = current_time()
        coin_spawn_timer = 0.0
        while self.running:
            now = current_time()
            dt = now - prev
            prev = now
            with self.lock:
                # update player positions
                for p in list(self.players.values()):
                    p.x += p.vx * dt
                    p.y += p.vy * dt
                    # bounds
                    p.x = max(10, min(WORLD_W-10, p.x))
                    p.y = max(10, min(WORLD_H-10, p.y))
                # spawn coins occasionally
                coin_spawn_timer += dt
                if coin_spawn_timer >= 2.0:  # spawn every ~2 seconds
                    coin_spawn_timer = 0.0
                    cx = random.uniform(30, WORLD_W-30)
                    cy = random.uniform(30, WORLD_H-30)
                    coin = {"id": self.coin_next_id, "x": cx, "y": cy}
                    self.coin_next_id += 1
                    self.coins.append(coin)

                # collisions: player-coin
                to_remove = []
                for coin in self.coins:
                    for p in self.players.values():
                        dx = coin["x"] - p.x
                        dy = coin["y"] - p.y
                        if (dx*dx + dy*dy) <= (PLAYER_RADIUS + COIN_RADIUS)**2:
                            # award score, remove coin
                            p.score += 1
                            to_remove.append(coin)
                            break
                if to_remove:
                    for c in to_remove:
                        if c in self.coins:
                            self.coins.remove(c)
            time.sleep(max(0, 1.0 / TICK_RATE - 0.0001))

    # Periodic broadcaster: send authoritative snapshot to all players at BROADCAST_HZ
    def broadcaster_loop(self):
        interval = 1.0 / BROADCAST_HZ
        while self.running:
            t0 = current_time()
            with self.lock:
                snapshot = {
                    "type": "snapshot",
                    "time": current_time(),
                    "players": {pid: {"x":p.x,"y":p.y,"score":p.score} for pid,p in self.players.items()},
                    "coins": [{"id":c["id"],"x":c["x"],"y":c["y"]} for c in self.coins]
                }
                data = json.dumps(snapshot).encode()
                # enqueue sends with SERVER_LATENCY (simulate send delay)
                send_time = current_time() + SERVER_LATENCY
                for p in self.players.values():
                    if p.addr:
                        self.outbound_queue.append((send_time, (data, p.addr)))
            elapsed = current_time() - t0
            to_sleep = interval - elapsed
            if to_sleep > 0:
                time.sleep(to_sleep)

if __name__ == "__main__":
    s = Server()
    s.start()
