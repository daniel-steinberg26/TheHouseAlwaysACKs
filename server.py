#!/usr/bin/env python3
"""server.py

- Broadcasts UDP offers once per second.
- Accepts TCP connections and plays N rounds per client.
- Prints game state on every change (per PDF requirement), including the current round.
- Ctrl+C shuts down cleanly: prints a single shutdown message, closes sockets, and avoids noisy tracebacks.
"""

from __future__ import annotations

import argparse
import signal
import socket
import threading
from dataclasses import dataclass
from typing import Optional, Tuple

from blackjack import (
    BlackJackGame,
    Card,
    RESULT_LOSS,
    RESULT_NOT_OVER,
    RESULT_WIN,
)

from common import (
    MAGIC_COOKIE,
    MSG_OFFER,
    MSG_REQUEST,
    MSG_PAYLOAD,
    UDP_PORT_OFFERS,
    OFFER_INTERVAL_SEC,
    OFFER_STRUCT,
    REQUEST_STRUCT,
    CLIENT_PAYLOAD_STRUCT,
    SERVER_PAYLOAD_STRUCT,
    SOCKET_TIMEOUT_SEC,
    pad_name,
    decode_name,
    recv_exact,
    card_to_wire,
    print_game_state,
)

# Using a TEST-NET address for local IP discovery. UDP 'connect' does not send traffic,
# but allows us to learn the preferred outbound interface/IP without hard-coding a real server IP.
IP_PROBE_TARGET = ("192.0.2.1", 80)  # RFC 5737 TEST-NET-1


@dataclass
class ServerConfig:
    server_name: str = "The House Always ACKs"
    tcp_port: int = 0  # 0 = auto


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(IP_PROBE_TARGET)
        ip = s.getsockname()[0]
        return ip or "127.0.0.1"
    except OSError:
        return "127.0.0.1"
    finally:
        try:
            s.close()
        except OSError:
            pass


def broadcast_offers(stop_evt: threading.Event, server_name: str, tcp_port: int) -> None:
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        payload = OFFER_STRUCT.pack(MAGIC_COOKIE, MSG_OFFER, tcp_port, pad_name(server_name))
        while not stop_evt.is_set():
            try:
                udp.sendto(payload, ("<broadcast>", UDP_PORT_OFFERS))
            except OSError:
                # Socket could be closing during shutdown; ignore.
                pass
            stop_evt.wait(OFFER_INTERVAL_SEC)
    finally:
        try:
            udp.close()
        except OSError:
            pass


def send_server_payload(sock: socket.socket, result: int, card: Optional[Card]) -> None:
    if card is None:
        rank, suit = 0, 0
    else:
        rank, suit = card_to_wire(card)
    msg = SERVER_PAYLOAD_STRUCT.pack(MAGIC_COOKIE, MSG_PAYLOAD, result, rank, suit)
    sock.sendall(msg)


def _print_state_with_round(
    round_idx: int,
    rounds_total: int,
    player_name: str,
    player_hand,
    dealer_hand,
    *,
    hide_dealer: bool,
) -> None:
    print_game_state(player_name + f" | Round {round_idx}/{rounds_total}", player_hand, dealer_hand, hide_dealer=hide_dealer)


def play_one_round(
    conn: socket.socket,
    server_name: str,
    round_idx: int,
    rounds_total: int,
    player_name: str,
    stop_evt: threading.Event,
) -> None:
    game = BlackJackGame(player_name)
    game.start_game()

    player_hand = game.get_player_hand()
    dealer_hand = game.get_dealer_hand()

    # Initial reveal: player 2 cards; dealer shows only first.
    p_cards = game.get_player_cards()
    d_cards = game.get_dealer_cards()

    send_server_payload(conn, RESULT_NOT_OVER, p_cards[0])
    send_server_payload(conn, RESULT_NOT_OVER, p_cards[1])
    send_server_payload(conn, RESULT_NOT_OVER, d_cards[0])

    _print_state_with_round(
        round_idx, rounds_total, player_name, player_hand, dealer_hand, hide_dealer=True
    )

    # Player decisions loop
    while not stop_evt.is_set():
        raw = recv_exact(conn, CLIENT_PAYLOAD_STRUCT.size, stop_event=stop_evt)
        if not raw:
            raise ConnectionError("client disconnected")

        cookie, msg_type, decision_raw = CLIENT_PAYLOAD_STRUCT.unpack(raw)
        if cookie != MAGIC_COOKIE or msg_type != MSG_PAYLOAD:
            continue

        decision = decision_raw.decode("utf-8", errors="ignore").strip("\x00")
        if decision not in ("Hittt", "Stand"):
            decision = "Stand"

        if decision == "Stand":
            break

        card, state = game.player_hit()
        if card is None:
            break

        send_server_payload(conn, state, card)
        _print_state_with_round(
            round_idx, rounds_total, player_name, player_hand, dealer_hand, hide_dealer=True
        )

        if state == RESULT_LOSS:
            # Bust: state already printed with the bust card (avoid printing the same final state twice).
            return

    # Dealer reveals hidden card then hits while < 17
    hidden = game.reveal_dealer_hidden()
    if hidden is not None:
        send_server_payload(conn, RESULT_NOT_OVER, hidden)

    _print_state_with_round(
        round_idx, rounds_total, player_name, player_hand, dealer_hand, hide_dealer=False
    )

    while not stop_evt.is_set() and game.dealer_should_hit():
        card, state = game.dealer_hit()
        if card is None:
            break

        send_server_payload(conn, state, card)
        _print_state_with_round(
            round_idx, rounds_total, player_name, player_hand, dealer_hand, hide_dealer=False
        )

        if state == RESULT_WIN:
            # Dealer bust: state already printed with the bust card (avoid printing the same final state twice).
            return

    result = game.final_result()
    send_server_payload(conn, result, None)
    _print_state_with_round(
        round_idx, rounds_total, player_name, player_hand, dealer_hand, hide_dealer=False
    )


def handle_client(
    conn: socket.socket,
    addr: Tuple[str, int],
    stop_evt: threading.Event,
    sockets_set: set,
    sockets_lock: threading.Lock,
    server_name: str,
) -> None:
    peer = f"{addr[0]}:{addr[1]}"
    try:
        conn.settimeout(SOCKET_TIMEOUT_SEC)

        req_raw = recv_exact(conn, REQUEST_STRUCT.size, stop_event=stop_evt)
        if not req_raw:
            return

        cookie, msg_type, rounds, client_name_raw = REQUEST_STRUCT.unpack(req_raw)

        # Validate request header (cookie + message type) to protect against malformed/foreign traffic
        if cookie != MAGIC_COOKIE or msg_type != MSG_REQUEST:
            return

        client_name = decode_name(client_name_raw)
        rounds = int(rounds) or 0
        if rounds <= 0:
            return

        print(f"[{peer}] Client '{client_name}' registered for {rounds} rounds")
        for r in range(1, rounds + 1):
            play_one_round(conn, server_name, r, rounds, client_name, stop_evt)
        print(f"[{peer}] Finished; closing")

    except (ConnectionError, OSError):
        if not stop_evt.is_set():
            print(f"[{peer}] Client disconnected")
    finally:
        with sockets_lock:
            sockets_set.discard(conn)
        try:
            conn.close()
        except OSError:
            pass


def run_server(server_name: str = "The House Always ACKs", tcp_port: int = 0) -> None:
    ip = get_local_ip()
    print(f"Server started, listening on IP address {ip}")

    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp.bind(("", tcp_port))
    tcp.listen()
    tcp.settimeout(SOCKET_TIMEOUT_SEC)
    port = tcp.getsockname()[1]

    stop_evt = threading.Event()
    shutdown_printed_evt = threading.Event()

    # Track active client sockets so we can close them on Ctrl+C without freezing.
    sockets_lock = threading.Lock()
    sockets_set: set[socket.socket] = set()

    # Track client threads so we can join them for a cleaner exit.
    threads_lock = threading.Lock()
    client_threads: set[threading.Thread] = set()

    def _shutdown(reason: str) -> None:
        # Ensure the shutdown message is printed exactly once.
        if not shutdown_printed_evt.is_set():
            shutdown_printed_evt.set()
            print("\nServer shutting down gracefully...")
        stop_evt.set()

        # Close listening socket to unblock accept().
        try:
            tcp.close()
        except OSError:
            pass

        # Close all active client sockets to unblock their recv loops.
        with sockets_lock:
            for s in list(sockets_set):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    s.close()
                except OSError:
                    pass
            sockets_set.clear()

    # Ctrl+C handler: do not raise KeyboardInterrupt noise, just initiate shutdown.
    previous_handler = signal.getsignal(signal.SIGINT)

    def _sigint_handler(signum, frame):  # type: ignore[no-untyped-def]
        _shutdown("sigint")
        # Do NOT chain to the previous handler, to avoid extra KeyboardInterrupt/tracebacks.

    try:
        signal.signal(signal.SIGINT, _sigint_handler)
    except Exception:
        # If signal registration fails, we still handle KeyboardInterrupt in the accept loop.
        pass

    offer_thread = threading.Thread(target=broadcast_offers, args=(stop_evt, server_name, port), daemon=True)
    offer_thread.start()
    print(f"Offering UDP broadcasts on port {UDP_PORT_OFFERS}, TCP listening on port {port}")

    try:
        while not stop_evt.is_set():
            try:
                conn, addr = tcp.accept()
            except socket.timeout:
                continue
            except OSError:
                # Listening socket likely closed during shutdown.
                break

            with sockets_lock:
                sockets_set.add(conn)

            th = threading.Thread(
                target=handle_client,
                args=(conn, addr, stop_evt, sockets_set, sockets_lock, server_name),
                daemon=False,
            )
            with threads_lock:
                client_threads.add(th)
            th.start()

    except KeyboardInterrupt:
        # Fallback path if signal handler wasn't installed for some reason.
        _shutdown("keyboardinterrupt")
    finally:
        _shutdown("finally")

        # Join client threads briefly to let them exit cleanly.
        with threads_lock:
            threads = list(client_threads)
        for th in threads:
            th.join(timeout=1.0)

        # Best effort: restore previous SIGINT handler.
        try:
            signal.signal(signal.SIGINT, previous_handler)
        except Exception:
            pass


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="The House Always ACKs")
    p.add_argument("--port", type=int, default=0)
    args = p.parse_args()

    # Wrap in try/except as a last safety net to avoid any top-level KeyboardInterrupt traceback.
    try:
        run_server(server_name=args.name, tcp_port=args.port)
    except KeyboardInterrupt:
        # If this ever happens, keep it quiet.
        print("\nServer shutting down gracefully...")


if __name__ == "__main__":
    main()
