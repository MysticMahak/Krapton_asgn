# client.py
# Pygame client: TCP handshake, UDP input + receive, interpolation, rendering
# Python 3.8+
import socket
import threading
import time
import json
import pygame
from collections import deque

SERVER_HOST = '127.0.0.1'
TCP_PORT = 9000
CLIENT_UDP_PORT = 0          # OS assigns random client port
CLIENT_LATENCY = 0.1         # 100 ms simulated delay
INTERPOLATION_DELAY = 0.1    # render ~100ms in past

SCREEN_W, SCREEN_H = 800, 600
PLAYER_RADIUS = 16
COIN_RADIUS = 10


def current_time():
    return time.time()


class Client:
    def __init__(self, server_host):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
        pygame.display.set_caption("Coin Collector (Pygame Client)")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont(None, 20)

        # TCP for handshake
        self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp.settimeout(None)  # wait indefinitely for start
        self.server_host = server_host
        self.server_udp_port = None
        self.pid = None

        # UDP for game updates
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(('', CLIENT_UDP_PORT))
        self.udp.setblocking(False)

        self.running = True

        # Latency queues
        self.to_send_queue = deque()   # (deliver_time, data, addr)
        self.inbound_queue = deque()   # (process_time, data)

        # Snapshot interpolation buffer
        self.snapshots = deque(maxlen=200)

    # ------------------- SHAPE DRAWING -------------------

    def draw_circle(self, x, y, color):
        pygame.draw.circle(self.screen, color, (int(x), int(y)), PLAYER_RADIUS)

    def draw_square(self, x, y, color):
        size = PLAYER_RADIUS * 2
        rect = pygame.Rect(int(x - PLAYER_RADIUS), int(y - PLAYER_RADIUS), size, size)
        pygame.draw.rect(self.screen, color, rect)

    def draw_triangle(self, x, y, color):
        p1 = (x, y - PLAYER_RADIUS)
        p2 = (x - PLAYER_RADIUS, y + PLAYER_RADIUS)
        p3 = (x + PLAYER_RADIUS, y + PLAYER_RADIUS)
        pygame.draw.polygon(self.screen, color, [p1, p2, p3])

    def draw_diamond(self, x, y, color):
        p1 = (x, y - PLAYER_RADIUS)
        p2 = (x - PLAYER_RADIUS, y)
        p3 = (x, y + PLAYER_RADIUS)
        p4 = (x + PLAYER_RADIUS, y)
        pygame.draw.polygon(self.screen, color, [p1, p2, p3, p4])

    def draw_shape(self, pid, x, y):
        # consistent shapes for all clients
        pid = int(pid)
        shape_type = pid % 4
        color = (0, 200, 255) if pid == self.pid else (200, 50, 50)

        if shape_type == 0:
            self.draw_circle(x, y, color)
        elif shape_type == 1:
            self.draw_square(x, y, color)
        elif shape_type == 2:
            self.draw_triangle(x, y, color)
        elif shape_type == 3:
            self.draw_diamond(x, y, color)

    # ------------------- STARTUP -------------------

    def start(self):
        try:
            print("Connecting TCP to server...", self.server_host)
            self.tcp.connect((self.server_host, TCP_PORT))

            welcome = self.tcp.recv(4096)
            msg = json.loads(welcome.decode())

            self.pid = msg["pid"]
            self.server_udp_port = msg["udp_port"]

            print(f"Connected as pid={self.pid}, server UDP port={self.server_udp_port}")

            # Wait for game start
            start_msg = self.tcp.recv(4096)
            print("Server says:", start_msg.decode())

        except Exception as e:
            print("TCP handshake failed:", e)
            return

        # Start UDP threads
        threading.Thread(target=self.udp_send_loop, daemon=True).start()
        threading.Thread(target=self.udp_recv_loop, daemon=True).start()

        self.game_loop()

    # ------------------- NETWORKING -------------------

    def udp_send_loop(self):
        while self.running:
            now = current_time()
            while self.to_send_queue and self.to_send_queue[0][0] <= now:
                _, data, addr = self.to_send_queue.popleft()
                try:
                    self.udp.sendto(data, addr)
                except:
                    pass
            time.sleep(0.001)

    def udp_recv_loop(self):
        while self.running:
            try:
                data, addr = self.udp.recvfrom(8192)
                process_time = current_time() + CLIENT_LATENCY
                self.inbound_queue.append((process_time, data))
            except BlockingIOError:
                pass

            now = current_time()
            while self.inbound_queue and self.inbound_queue[0][0] <= now:
                _, data = self.inbound_queue.popleft()
                try:
                    snap = json.loads(data.decode())
                except:
                    continue

                if snap.get("type") == "snapshot":
                    snap["recv_time"] = current_time()
                    self.snapshots.append(snap)

            time.sleep(0.001)

    def send_intent(self, intent):
        msg = {"type": "input", "pid": self.pid, "intent": intent}
        data = json.dumps(msg).encode()
        addr = (self.server_host, self.server_udp_port)

        delay_until = current_time() + CLIENT_LATENCY
        self.to_send_queue.append((delay_until, data, addr))

    # ------------------- INTERPOLATION -------------------

    def get_interpolated_state(self):
        if not self.snapshots:
            return {}, []

        render_time = current_time() - INTERPOLATION_DELAY
        snaps = list(self.snapshots)

        s0 = None
        s1 = None

        # Find surrounding snapshots
        for i in range(len(snaps) - 1):
            if snaps[i]["time"] <= render_time <= snaps[i + 1]["time"]:
                s0 = snaps[i]
                s1 = snaps[i + 1]
                break

        if s0 is None:
            # No interpolation possible yet → use last snapshot
            s = snaps[-1]
            return s["players"], s["coins"]

        t0 = s0["time"]
        t1 = s1["time"]
        alpha = (render_time - t0) / (t1 - t0) if t1 > t0 else 1
        alpha = max(0, min(1, alpha))

        interp_players = {}

        for pid, p0 in s0["players"].items():
            p1 = s1["players"].get(pid, p0)
            x = p0["x"] * (1 - alpha) + p1["x"] * alpha
            y = p0["y"] * (1 - alpha) + p1["y"] * alpha
            score = p1["score"]
            interp_players[int(pid)] = {"x": x, "y": y, "score": score}

        coins = s1["coins"]
        return interp_players, coins

    # ------------------- RENDERING -------------------

    def draw_text(self, text, x, y):
        img = self.font.render(text, True, (255, 255, 255))
        self.screen.blit(img, (x, y))

    # ------------------- MAIN LOOP -------------------

    def game_loop(self):
        while self.running:
            dt = self.clock.tick(60) / 1000.0

            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self.running = False

            keys = pygame.key.get_pressed()
            intent = {
                "left": keys[pygame.K_a] or keys[pygame.K_LEFT],
                "right": keys[pygame.K_d] or keys[pygame.K_RIGHT],
                "up": keys[pygame.K_w] or keys[pygame.K_UP],
                "down": keys[pygame.K_s] or keys[pygame.K_DOWN],
            }
            self.send_intent(intent)

            players, coins = self.get_interpolated_state()

            self.screen.fill((18, 18, 18))

            # Draw coins
            for c in coins:
                pygame.draw.circle(
                    self.screen, (255, 200, 0), (int(c["x"]), int(c["y"])), COIN_RADIUS
                )

            # Draw players
            for pid, p in players.items():
                self.draw_shape(pid, p["x"], p["y"])
                self.draw_text(
                    f"P{pid} S:{p['score']}", p["x"] + 18, p["y"] - 8
                )

            self.draw_text(f"You: P{self.pid}", 10, 10)

            pygame.display.flip()

        pygame.quit()


if __name__ == "__main__":
    Client(SERVER_HOST).start()
