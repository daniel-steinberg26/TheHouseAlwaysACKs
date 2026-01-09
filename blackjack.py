"""blackjack.py

Core blackjack domain model (no networking):
- Card / Deck / Hand / Player / Dealer
- BlackJackGame provides round orchestration for the server

Rules implemented:
- Ace counts as 1 or 11 (best non-busting value)
- J/Q/K are worth 10
- Dealer hits until reaching 17+
"""

import random
from typing import Optional, List

# Simplified blackjack rules:
# - Ace is 1 or 11 (real blackjack)
# - Face cards are 10
# - Dealer hits until reaching 17+

# IMPORTANT: suit order here matches the network encoding used in common.py:
# Hearts=0, Diamonds=1, Clubs=2, Spades=3
SUITS = ["Hearts", "Diamonds", "Clubs", "Spades"]
VALUES = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

BLACKJACK = 21
DEALER_MAX = 17
ROYALTY_VALUE = 10
ACE_VALUE = 11

# Result codes (shared with protocol)
RESULT_NOT_OVER = 0x0
RESULT_TIE = 0x1
RESULT_LOSS = 0x2
RESULT_WIN = 0x3


class Card:
    def __init__(self, value: str, suit: str):
        if value not in VALUES:
            raise ValueError(f"Invalid card value: {value}")
        if suit not in SUITS:
            raise ValueError(f"Invalid card suit: {suit}")
        self.value = value
        self.suit = suit

    @staticmethod
    def from_wire(rank: int, suit_code: int) -> "Card":
        # rank: 1..13 (A..K), suit_code: 0..3 (Hearts, Diamonds, Clubs, Spades)
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
        suit = SUITS[suit_code] if 0 <= suit_code < len(SUITS) else "Hearts"
        return Card(value, suit)

    def __repr__(self) -> str:
        return f"Card({self.value!r}, {self.suit!r})"


class Deck:
    def __init__(self):
        self.cards = [Card(value, suit) for suit in SUITS for value in VALUES]
        random.shuffle(self.cards)

    def deal_card(self) -> Optional[Card]:
        return self.cards.pop() if self.cards else None


class Hand:
    def __init__(self, cards: Optional[List[Card]] = None):
        # copy to avoid aliasing
        self.cards: List[Card] = list(cards) if cards is not None else []

    def add_card(self, card: Card) -> None:
        self.cards.append(card)

    def calculate_value(self) -> int:
        total = 0
        aces = 0

        for card in self.cards:
            if card.value in ("J", "Q", "K"):
                total += ROYALTY_VALUE
            elif card.value == "A":
                total += ACE_VALUE
                aces += 1
            else:
                total += int(card.value)

        # Convert Aces from 11 -> 1 as needed
        while total > BLACKJACK and aces > 0:
            total -= ROYALTY_VALUE
            aces -= 1

        return total


class Player:
    def __init__(self, name: str):
        self.name = name
        self.hand = Hand()

    def draw_card(self, card: Card) -> None:
        self.hand.add_card(card)

    def get_hand_value(self) -> int:
        return self.hand.calculate_value()

    def is_busted(self) -> bool:
        return self.get_hand_value() > BLACKJACK


class Dealer(Player):
    def __init__(self):
        super().__init__("Dealer")

    def should_hit(self) -> bool:
        return self.get_hand_value() < DEALER_MAX


class BlackJackGame:
    """Single source of truth for a round."""

    def __init__(self, player_name: str):
        self.deck = Deck()
        self.dealer = Dealer()
        self.player = Player(player_name)

    def get_player_hand(self) -> Hand:
        return self.player.hand

    def get_dealer_hand(self) -> Hand:
        return self.dealer.hand

    def get_player_cards(self) -> List[Card]:
        return list(self.player.hand.cards)

    def get_dealer_cards(self) -> List[Card]:
        return list(self.dealer.hand.cards)

    def start_game(self):
        for _ in range(2):
            self.player.draw_card(self.deck.deal_card())
            self.dealer.draw_card(self.deck.deal_card())

    def player_hit(self):
        card = self.deck.deal_card()
        if card is None:
            return None, RESULT_NOT_OVER
        self.player.draw_card(card)
        return card, (RESULT_LOSS if self.player.is_busted() else RESULT_NOT_OVER)

    def dealer_should_hit(self) -> bool:
        return self.dealer.should_hit()

    def reveal_dealer_hidden(self):
        cards = self.dealer.hand.cards
        return cards[1] if len(cards) > 1 else None

    def dealer_hit(self):
        card = self.deck.deal_card()
        if card is None:
            return None, RESULT_NOT_OVER
        self.dealer.draw_card(card)
        return card, (RESULT_WIN if self.dealer.is_busted() else RESULT_NOT_OVER)

    # resolution
    def final_result(self) -> int:
        p = self.player.get_hand_value()
        d = self.dealer.get_hand_value()

        if p > BLACKJACK:
            return RESULT_LOSS
        if d > BLACKJACK:
            return RESULT_WIN
        if p > d:
            return RESULT_WIN
        if d > p:
            return RESULT_LOSS
        return RESULT_TIE
