import asyncio
import json
import random
import websockets
import os
import logging

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

class Deck:
    def __init__(self):
        ranks = ['6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        suits = ['♥', '♦', '♣', '♠']
        self.cards = [f"{rank}{suit}" for rank in ranks for suit in suits]
        random.shuffle(self.cards)

    def draw(self, count=1):
        drawn_cards = [self.cards.pop(0) for _ in range(min(count, len(self.cards)))]
        return drawn_cards

    def is_empty(self):
        return len(self.cards) == 0

class Player:
    def __init__(self, name, websocket):
        self.name = name
        self.websocket = websocket
        self.hand = []
        self.collected_sets = []
        self.collected_boxes = 0

class Game:
    def __init__(self):
        self.players = {}
        self.deck = Deck()
        self.game_started = False
        self.current_turn_index = 0
        self.asking_player = None
        self.target_player = None
        self.asked_card_rank = None
        self.guessed_count = 0

    async def add_player(self, player_name, websocket):
        self.players[player_name] = Player(player_name, websocket)
        
    def remove_player(self, player_name):
        if player_name in self.players:
            del self.players[player_name]
            
    async def notify_all(self, message):
        if self.players:
            await asyncio.gather(
                *[player.websocket.send(json.dumps({"type": "log", "message": message})) for player in self.players.values()]
            )

    async def notify_player(self, player_name, message_type, data):
        if player_name in self.players:
            await self.players[player_name].websocket.send(json.dumps({"type": message_type, **data}))

    async def notify_all_state(self):
        if not self.game_started:
            for player in self.players.values():
                state = self.get_state_for_player(player.name)
                await player.websocket.send(json.dumps({"type": "update_state", "state": state}))
            return

        for player_name, player in self.players.items():
            state = self.get_state_for_player(player_name)
            await player.websocket.send(json.dumps({"type": "update_state", "state": state}))
            
    def get_state_for_player(self, player_name):
        players_list = [{"name": p.name, "is_turn": p.name == self.get_current_turn_player_name(), "collected_boxes": p.collected_boxes, "collected_sets": p.collected_sets} for p in self.players.values()]
        
        my_hand = self.players[player_name].hand if player_name in self.players else []
        
        return {
            "game_started": self.game_started,
            "players": players_list,
            "deck_size": len(self.deck.cards),
            "current_turn": self.get_current_turn_player_name(),
            "room_admin": next(iter(self.players), None),
            "my_hand": my_hand
        }

    def get_current_turn_player_name(self):
        if self.game_started and self.players:
            player_names = list(self.players.keys())
            return player_names[self.current_turn_index % len(player_names)]
        return None

    def start_game(self):
        if len(self.players) >= 2 and not self.game_started:
            self.game_started = True
            self.deck = Deck()
            for player in self.players.values():
                player.hand = self.deck.draw(6)
            
            self.current_turn_index = 0
            
            logger.info("Гра розпочалась")
            return True
        return False
        
    def check_for_sets(self, player_name):
        player = self.players.get(player_name)
        if not player:
            return
        
        counts = {}
        for card in player.hand:
            rank = card[:-1]
            counts[rank] = counts.get(rank, 0) + 1
        
        collected = []
        for rank, count in counts.items():
            if count == 4:
                collected.append(rank)
        
        if collected:
            for rank_to_remove in collected:
                player.hand = [card for card in player.hand if card[:-1] != rank_to_remove]
                player.collected_sets.append(rank_to_remove)
                player.collected_boxes += 1
            return True
        return False

    async def handle_ask_card(self, asking_player_name, target_player_name, card_rank):
        self.asking_player = asking_player_name
        self.target_player = target_player_name
        self.asked_card_rank = card_rank
        
        await self.notify_player(
            target_player_name,
            "ask_response_needed",
            {"asking_player": asking_player_name, "card_rank": card_rank}
        )

    async def handle_ask_response(self, player_name, response):
        if player_name != self.target_player:
            return

        asking_player = self.players.get(self.asking_player)
        target_player = self.players.get(self.target_player)
        
        if response == "yes":
            # Передаємо керування на вгадування кількості карт
            await self.notify_all(f"Гравець {self.target_player} має карти рангу {self.asked_card_rank}.")
            await self.notify_player(
                self.asking_player,
                "guess_count_needed",
                {"target_player": self.target_player, "card_rank": self.asked_card_rank}
            )
        else:
            # Гравець відповів "Ні"
            await self.notify_all(f"Гравець {self.target_player} не має карт рангу {self.asked_card_rank}.")
            
            # Гравець бере карту з колоди
            drawn_card = self.deck.draw()
            if drawn_card:
                asking_player.hand.extend(drawn_card)
                await self.notify_all(f"Гравець {self.asking_player} бере карту з колоди.")
            
            self.check_for_sets(self.asking_player)
            
            # Перехід до наступного ходу
            self.current_turn_index += 1
            
        # Скидання стану запиту та оновлення інтерфейсу для всіх
        self.asking_player = None
        self.target_player = None
        self.asked_card_rank = None
        await self.notify_all_state()

    async def handle_guess_count(self, player_name, count):
        if player_name != self.asking_player:
            return
        
        self.guessed_count = int(count)
        
        target_player = self.players.get(self.target_player)
        cards_of_rank = [card for card in target_player.hand if card[:-1] == self.asked_card_rank]
        
        if len(cards_of_rank) == self.guessed_count:
            await self.notify_all(f"Гравець {self.asking_player} вгадав кількість! Вгадування мастей...")
            await self.notify_player(
                self.asking_player,
                "guess_suits_needed",
                {"card_rank": self.asked_card_rank, "count": self.guessed_count}
            )
        else:
            await self.notify_all(f"Гравець {self.asking_player} не вгадав кількість. Хід переходить до наступного гравця.")
            self.current_turn_index += 1
            self.asking_player = None
            self.target_player = None
            self.asked_card_rank = None
            await self.notify_all_state()

    async def handle_guess_suits(self, player_name, suits):
        if player_name != self.asking_player:
            return
        
        target_player = self.players.get(self.target_player)
        cards_of_rank = [card for card in target_player.hand if card[:-1] == self.asked_card_rank]
        
        correct_suits = [card[-1] for card in cards_of_rank]
        guessed_suits = suits
        
        matched_suits = set(correct_suits) & set(guessed_suits)
        
        if len(matched_suits) == self.guessed_count:
            await self.notify_all(f"Гравець {self.asking_player} вгадав усі масті! Всі карти переміщуються.")
            
            # Передача карт
            for card in cards_of_rank:
                target_player.hand.remove(card)
                self.players[self.asking_player].hand.append(card)
            
            self.check_for_sets(self.asking_player)

            # Хід залишається у гравця
            self.asking_player = None
            self.target_player = None
            self.asked_card_rank = None
        else:
            await self.notify_all(f"Гравець {self.asking_player} не вгадав масті. Хід переходить до наступного гравця.")
            self.current_turn_index += 1
            self.asking_player = None
            self.target_player = None
            self.asked_card_rank = None
            
        await self.notify_all_state()

game_rooms = {}

async def handler(websocket, path):
    room_id = None
    player_name = None
    game = None

    try:
        while True:
            message = await websocket.recv()
            data = json.loads(message)
            
            if data['type'] == 'join':
                player_name = data['name']
                room_id = data['room']
                
                if room_id not in game_rooms:
                    game_rooms[room_id] = Game()
                
                game = game_rooms[room_id]
                await game.add_player(player_name, websocket)
                await websocket.send(json.dumps({"type": "joined_room", "room_id": room_id}))
                await game.notify_all(f"Гравець {player_name} приєднався до гри.")
                await game.notify_all_state()
            
            elif game:
                if data['type'] == 'start_game' and player_name == next(iter(game.players), None):
                    if game.start_game():
                        await game.notify_all(f"Гра розпочалась! Перший хід за {game.get_current_turn_player_name()}")
                        await game.notify_all_state()
                        
                elif data['type'] == 'ask_card' and player_name == game.get_current_turn_player_name():
                    await game.handle_ask_card(player_name, data['target'], data['card_rank'])

                elif data['type'] == 'ask_response' and player_name == game.target_player:
                    await game.handle_ask_response(player_name, data['response'])
                
                elif data['type'] == 'guess_count' and player_name == game.asking_player:
                    await game.handle_guess_count(player_name, data['count'])
                
                elif data['type'] == 'guess_suits' and player_name == game.asking_player:
                    await game.handle_guess_suits(player_name, data['suits'])

    except websockets.exceptions.ConnectionClosedError:
        logger.info(f"З'єднання закрито для гравця {player_name} в кімнаті {room_id}")
    finally:
        if player_name and room_id and room_id in game_rooms:
            game = game_rooms[room_id]
            game.remove_player(player_name)
            if not game.players:
                del game_rooms[room_id]
                logger.info(f"Кімната {room_id} закрита, оскільки всі гравці вийшли.")
            else:
                await game.notify_all(f"Гравець {player_name} відключився.")
                await game.notify_all_state()

async def main():
    port_env = os.environ.get("PORT")
    port = int(port_env) if port_env else 8765
    logging.info(f"Starting WebSocket server on 0.0.0.0:{port}")
    async with websockets.serve(handler, "0.0.0.0", port):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
