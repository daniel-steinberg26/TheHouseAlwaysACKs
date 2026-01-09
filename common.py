from __future__ import annotations

"""common.py

Shared protocol constants, struct formats, helpers, and shared pretty-printing.

The printing helpers operate on blackjack.py objects (Card/Hand) rather than tuples.
"""

import socket
import struct
from dataclasses import dataclass
from typing import Optional, Tuple

from blackjack import Card, Hand

# ---- Protocol constants ----
MAGIC_COOKIE = 0xABCDDCBA
MSG_OFFER = 0x2
MSG_REQUEST = 0x3
MSG_PAYLOAD = 0x4

RESULT_NOT_OVER = 0x0
RESULT_TIE = 0x1
RESULT_LOSS = 0x2
RESULT_WIN = 0x3

UDP_PORT_OFFERS = 13122
OFFER_INTERVAL_SEC = 1.0

# Default socket timeout used by client/server loops (keeps Ctrl+C responsive without busy-waiting)
SOCKET_TIMEOUT_SEC = 1.0

# Suit encoding per spec "HDCS" (must match blackjack.SUITS order)
SUIT_TO_CODE = {"Hearts": 0, "Diamonds": 1, "Clubs": 2, "Spades": 3}
CODE_TO_SUIT = {v: k for k, v in SUIT_TO_CODE.items()}

# ---- Binary layouts (network byte order, big-endian) ----
OFFER_STRUCT = struct.Struct("!IBH32s")         # cookie, type, tcp_port, server_name[32]
REQUEST_STRUCT = struct.Struct("!IBB32s")       # cookie, type, rounds, client_name[32]
CLIENT_PAYLOAD_STRUCT = struct.Struct("!IB5s")  # cookie, type, decision[5] ("Hittt"/"Stand")
SERVER_PAYLOAD_STRUCT = struct.Struct("!IBBHB") # cookie, type, result, rank(u16), suit(u8)


def pad_name(name: str, length: int = 32) -> bytes:
    raw = name.encode("utf-8", errors="ignore")[:length]
    return raw.ljust(length, b"\x00")


def decode_name(raw32: bytes) -> str:
    return raw32.split(b"\x00", 1)[0].decode("utf-8", errors="ignore")


def recv_exact(sock: socket.socket, n: int, stop_event=None) -> Optional[bytes]:
    """Receive exactly n bytes over TCP.

    Returns None if the connection closes before n bytes arrive.
    - If the socket times out, the function keeps waiting (not busy-waiting).
    - If stop_event is provided and set, the function returns None to allow graceful shutdown.
    """
    data = bytearray()
    while len(data) < n:
        if stop_event is not None and stop_event.is_set():
            return None
        try:
            chunk = sock.recv(n - len(data))
        except socket.timeout:
            continue
        except OSError:
            return None
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


def card_to_wire(card: Card) -> Tuple[int, int]:
    """Convert Card(value,suit) -> (rank_1_13, suit_0_3)."""
    val = card.value
    if val == "A":
        rank = 1
    elif val == "J":
        rank = 11
    elif val == "Q":
        rank = 12
    elif val == "K":
        rank = 13
    else:
        rank = int(val)
    suit_code = SUIT_TO_CODE.get(card.suit, 0)
    return rank, suit_code


def card_from_wire(rank: int, suit_code: int) -> Optional[Card]:
    """Convert (rank,suit) from the network into a Card object. Returns None for 0/0."""
    if rank == 0:
        return None
    suit = CODE_TO_SUIT.get(suit_code, "Hearts")
    if rank == 1:
        value = "A"
    elif rank == 11:
        value = "J"
    elif rank == 12:
        value = "Q"
    elif rank == 13:
        value = "K"
    else:
        value = str(rank)
    return Card(value, suit)


def card_text(card: Card) -> str:
    # Pretty single-token card representation for terminal output
    suit_symbol = {
        'Hearts': '♥',
        'Diamonds': '♦',
        'Clubs': '♣',
        'Spades': '♠',
    }.get(card.suit, '?')
    return f"{card.value}{suit_symbol}"


def _format_hand(title: str, hand: Hand, hide_second: bool = False) -> str:
    parts = []
    for i, c in enumerate(hand.cards):
        if hide_second and i == 1:
            parts.append("?")
        else:
            parts.append(card_text(c))
    if len(hand.cards) == 1 and hide_second:
        parts.append("?")
    return f"{title}: " + ", ".join(parts)


def format_state(player_hand: Hand, dealer_hand: Hand, hide_dealer: bool) -> str:
    p_total = player_hand.calculate_value()
    if hide_dealer:
        d_visible = Hand([dealer_hand.cards[0]])
        d_total = d_visible.calculate_value()
        suffix = "+"
    else:
        d_total = dealer_hand.calculate_value()
        suffix = ""

    return (
        f"{_format_hand('Player', player_hand)}   (total: {p_total})\n"
        f"{_format_hand('Dealer', dealer_hand, hide_second=hide_dealer)}   (total: {d_total}{suffix})"
    )


def print_game_state(player_name: Optional[str], player_hand: Hand, dealer_hand: Hand, hide_dealer: bool) -> None:
    # Server prints are wrapped with the player name; client prints are separated by === lines.
    if player_name:
        print(f"========= {player_name} =========")
        print(format_state(player_hand, dealer_hand, hide_dealer))
        print("=======================")
    else:
        print("=======================")
        print(format_state(player_hand, dealer_hand, hide_dealer))
        print("=======================")


@dataclass
class Offer:
    server_ip: str
    server_port: int
    server_name: str