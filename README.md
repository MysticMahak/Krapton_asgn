# Multiplayer Coin Collector (Pygame + Python server)

## Overview
This is a minimal authoritative multiplayer demo that meets the test requirements:
- Authoritative server (positions, coins, scoring).
- Clients send only intent/input.
- Server resolves collisions.
- Simulated network latency ~200 ms (100ms server + 100ms client).
- UDP for state updates; TCP for lobby/handshake.
- Client uses Pygame for rendering and interpolation.

## Files
- server.py : authoritative server (TCP + UDP).
- client.py : Pygame client (TCP handshake + UDP input/state).
- (This README)

## Requirements
- Python 3.8+
- pygame (`pip install pygame`)
- Run on same machine for quick test; change `SERVER_HOST` in client.py to server IP when remote.

## How to run
1. Start server:
   ```
   python server.py
   ```
2. Start two clients (each in its own terminal):
   ```
   python client.py
   ```
   ```
   python client.py
   ```
The server waits for 2 players and then auto-starts.

## Controls
- WASD or Arrow keys to move.

## Notes on latency simulation
- The server simulates ~100 ms delay on inbound processing and ~100 ms delay before sending snapshots.
- The client simulates ~100 ms delay on sending inputs and ~100 ms delay when processing inbound snapshots.
- This results in approximately 200 ms RTT (good for testing interpolation).

## How interpolation works
- The client keeps received snapshots in a buffer.
- The renderer draws the world at `now - 100ms` (INTERPOLATION_DELAY).
- Linear interpolation between two snapshots is used to smooth remote players.

## Extensions & Improvements
- Add sequence numbers and packet loss handling.
- Use UDP reliability for important messages.
- Add client-side prediction for the local player.
- Add ping measurement and dynamic interpolation delay.
