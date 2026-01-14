#!/usr/bin/env python3
"""client.py

- Listens for UDP offers
- Connects via TCP and plays N rounds
- Client does NOT hold the game logic, but it *does* hold Hand objects for display.
- Prints the FULL state every time it changes (pretty format via py)
- Ctrl+C exits cleanly (no traceback)
- If server disconnects, prints a friendly message and returns to listening
"""

from __future__ import annotations
import argparse

import socket
import signal
import threading
from typing import Optional, Tuple

from blackjack import Hand, RESULT_LOSS, RESULT_NOT_OVER, RESULT_TIE, RESULT_WIN
from common import (
    MAGIC_COOKIE,
    MSG_OFFER,
    MSG_REQUEST,
    MSG_PAYLOAD,
    UDP_PORT_OFFERS,
    OFFER_STRUCT,
    REQUEST_STRUCT,
    CLIENT_PAYLOAD_STRUCT,
    SERVER_PAYLOAD_STRUCT,
    SOCKET_TIMEOUT_SEC,
    pad_name,
    decode_name,
    recv_exact,
    Offer,
    print_game_state,
    card_from_wire,
)
_running = True
_stop_evt = threading.Event()
_shutdown_printed = False
_active_tcp: Optional[socket.socket] = None


def _sigint_handler(signum, frame):
    """Handle Ctrl+C: print once, stop loops, and close active sockets to unblock recv()."""
    global _running, _shutdown_printed, _active_tcp
    _running = False
    _stop_evt.set()
    if not _shutdown_printed:
        _shutdown_printed = True
        print("Client shutting down...")
    # Close active TCP socket (if any) to unblock recv_exact immediately.
    if _active_tcp is not None:
        try:
            _active_tcp.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            _active_tcp.close()
        except OSError:
            pass
        _active_tcp = None


signal.signal(signal.SIGINT, _sigint_handler)


def listen_for_offer() -> Optional[Offer]:
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.bind(("", UDP_PORT_OFFERS))
        udp.settimeout(SOCKET_TIMEOUT_SEC)
        print("Client started, listening for offer requests...")

        while _running:
            try:
                data, (ip, _) = udp.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                return None

            if len(data) < OFFER_STRUCT.size:
                continue

            cookie, msg_type, tcp_port, name_raw = OFFER_STRUCT.unpack(data[: OFFER_STRUCT.size])
            if cookie != MAGIC_COOKIE or msg_type != MSG_OFFER:
                continue

            name = decode_name(name_raw)
            print(f"Received offer from {ip} ({name})")
            return Offer(server_ip=ip, server_port=int(tcp_port), server_name=name)

        return None
    except:
        return None
    finally:
        try:
            udp.close()
        except OSError:
            pass


def ask_rounds() -> int:
    while _running:
        try:
            v = int(input("How many rounds do you want to play? "))
            if 1 <= v <= 255:
                return v
            print("Please enter 1..255")
        except ValueError:
            print("Please enter an integer.")
        except (EOFError, KeyboardInterrupt):
            return 0
    return 0


def ask_decision() -> str:
    while _running:
        try:
            ans = input("Hit or Stand? ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            # Ctrl+C should exit immediately; do not send anything to server.
            return ""

        if ans in ("hit", "h"):
            return "Hittt"
        if ans in ("stand", "s"):
            return "Stand"
        print("Type Hit or Stand.")
    return "Stand"


def recv_payload(tcp: socket.socket) -> Optional[Tuple[int, int, int]]:
    raw = recv_exact(tcp, SERVER_PAYLOAD_STRUCT.size, stop_event=_stop_evt)
    if not raw:
        return None

    cookie, msg_type, result, rank, suit = SERVER_PAYLOAD_STRUCT.unpack(raw)

    # Validate payload header; ignore unexpected messages instead of crashing.
    if cookie != MAGIC_COOKIE or msg_type != MSG_PAYLOAD:
        return None

    return result, rank, suit


def play_session(offer: Offer, rounds: int, client_name: str) -> None:
    global _active_tcp
    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _active_tcp = tcp
    try:
        tcp.settimeout(SOCKET_TIMEOUT_SEC)
        tcp.connect((offer.server_ip, offer.server_port))
        tcp.sendall(REQUEST_STRUCT.pack(MAGIC_COOKIE, MSG_REQUEST, rounds, pad_name(client_name)))

        wins = 0
        played = 0

        for r in range(1, rounds + 1):
            if not _running:
                return

            print(f"\n=== Round {r}/{rounds} ===")

            player_hand = Hand()
            dealer_hand = Hand()

            # initial 3 payloads: player, player, dealer-up
            for i in range(3):
                pkt = recv_payload(tcp)
                if pkt is None:
                    raise ConnectionError("server disconnected")
                result, rank, suit = pkt
                card = card_from_wire(rank, suit)
                if card is None:
                    continue
                if i < 2:
                    player_hand.add_card(card)
                else:
                    dealer_hand.add_card(card)

            # Print full state (dealer hidden card not known yet)
            print_game_state(None, player_hand, dealer_hand, hide_dealer=True)

            # Player loop
            while _running:
                decision = ask_decision()
                if not _running or not decision:
                    return
                tcp.sendall(CLIENT_PAYLOAD_STRUCT.pack(MAGIC_COOKIE, MSG_PAYLOAD, decision.encode("utf-8")))

                pkt = recv_payload(tcp)
                if pkt is None:
                    raise ConnectionError("server disconnected")

                result, rank, suit = pkt

                if decision == "Hittt":
                    c = card_from_wire(rank, suit)
                    if c is not None:
                        player_hand.add_card(c)

                    print_game_state(None, player_hand, dealer_hand, hide_dealer=True)

                    if result != RESULT_NOT_OVER:
                        outcome = {RESULT_WIN: "WIN", RESULT_LOSS: "LOSS", RESULT_TIE: "TIE"}.get(result, "?")
                        print("Result: " + outcome)
                        played += 1
                        if result == RESULT_WIN:
                            wins += 1
                        break

                else:
                    # Stand: first payload may be dealer hidden card
                    c = card_from_wire(rank, suit)
                    if c is not None:
                        dealer_hand.add_card(c)

                    print_game_state(None, player_hand, dealer_hand, hide_dealer=False)

                    # Read dealer draws until final result
                    while _running and result == RESULT_NOT_OVER:
                        pktd = recv_payload(tcp)
                        if pktd is None:
                            raise ConnectionError("server disconnected")
                        result, rank, suit = pktd
                        c = card_from_wire(rank, suit)
                        if c is not None:
                            dealer_hand.add_card(c)
                            print_game_state(None, player_hand, dealer_hand, hide_dealer=False)

                    outcome = {RESULT_WIN: "WIN", RESULT_LOSS: "LOSS", RESULT_TIE: "TIE"}.get(result, "?")
                    print("Result: " + outcome)
                    played += 1
                    if result == RESULT_WIN:
                        wins += 1
                    break


        # Session stats (after all rounds complete)
        if played > 0:
            win_rate = wins / played * 100
            print(f"Finished playing {played} rounds, win rate: {win_rate:.2f}%")

    except (ConnectionError, OSError):
        if _running:
            print("\nServer disconnected. Returning to listening mode...\n")
    finally:
        try:
            tcp.close()
        except OSError:
            pass
        _active_tcp = None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="Team Joker")
    args = p.parse_args()

    while _running:
        rounds = ask_rounds()
        if rounds <= 0 or not _running:
            break

        offer = listen_for_offer()
        if not offer:
            if _running:
                print("No offers received; retrying...")
            continue

        play_session(offer, rounds, args.name)


if __name__ == "__main__":
    main()
