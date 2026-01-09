import random

# Minimal blackjack engine used by server.
# Follows the simplified rules in the assignment: Ace = 11 always, no special blackjack rule.

SUITS = ["Spades", "Hearts", "Diamonds", "Clubs"]
VALUES = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

BLACKJACK = 21
DEALER_MAX = 17
ROYALTY_VALUE = 10
ACE_VALUE = 11

class Card:
    def __init__(self, value, suit):
        self.value = value
        self.suit = suit

class Deck:
    def __init__(self):
        self.cards = [Card(value, suit) for suit in SUITS for value in VALUES]

    def shuffle(self):
        random.shuffle(self.cards)

    def deal_card(self):
        return self.cards.pop() if self.cards else None

class Hand:
    def __init__(self):
        self.cards = []

    def add_card(self, card):
        self.cards.append(card)

    def calculate_value(self):
        value = 0
        for card in self.cards:
            if card.value in ["J", "Q", "K"]:
                value += ROYALTY_VALUE
            elif card.value == "A":
                value += ACE_VALUE
            else:
                value += int(card.value)
        return value

class Player:
    def __init__(self, name):
        self.name = name
        self.hand = Hand()

    def draw_card(self, card):
        self.hand.add_card(card)

    def get_hand_value(self):
        return self.hand.calculate_value()

class Dealer(Player):
    def __init__(self):
        super().__init__("Dealer")

class BlackJackGame:
    def __init__(self, player_name):
        self.deck = Deck()
        self.deck.shuffle()
        self.dealer = Dealer()
        self.player = Player(player_name)

    def start_game(self):
        for _ in range(2):
            self.player.draw_card(self.deck.deal_card())
            self.dealer.draw_card(self.deck.deal_card())

    def player_hit(self):
        self.player.draw_card(self.deck.deal_card())
        return self.player.get_hand_value() <= BLACKJACK