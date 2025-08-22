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

class Game:
    def __init__(self):
        self.players = {}
        self.deck = Deck()
        self.game_started = False
        self.current_turn_index = 0
        self.asking_player = None
        self.target_player = None
        self.asked_rank = None
        self.room_admin = None

    async def add_player(self, name, websocket):
        if not self.game_started and len(self.players) < 6:
            if name in self.players:
                return False, "Гравець з таким ім'ям вже є в кімнаті."
            player = Player(name, websocket)
            self.players[name] = player
            if self.room_admin is None:
                self.room_admin = name
            return True, f"Гравець {name} приєднався."
        elif self.game_started:
            return False, "Гра вже розпочалась."
        else:
            return False, "Кімната повна."

    def remove_player(self, name):
        if name in self.players:
            del self.players[name]
            if name == self.room_admin:
                self.room_admin = next(iter(self.players), None)

    async def start_game(self):
        if len(self.players) >= 2 and not self.game_started:
            self.game_started = True
            self.deck = Deck()
            await self.deal_initial_cards()
            player_names = list(self.players.keys())
            self.current_turn_index = 0
            self.asking_player = player_names[self.current_turn_index]
            await self.notify_all("Гра розпочалась! Перший хід за " + self.asking_player)
            
            # Перевіряємо, чи має перший гравець карти, щоб розпочати хід
            await self.check_and_deal_if_needed(self.asking_player)
            
            await self.notify_all_state()
            return True
        return False
    
    async def deal_initial_cards(self):
        num_players = len(self.players)
        cards_to_deal = 4 #5 if num_players <= 3 else 4
        
        for _ in range(cards_to_deal):
            for player_name in self.players:
                card = self.deck.draw()[0]
                self.players[player_name].hand.append(card)
        
        for player_name in self.players:
            player = self.players[player_name]
            if self.check_for_sets(player):
                await self.notify_all(f"Гравець {player.name} зібрав скриньку під час роздачі!")

    def check_for_sets(self, player):
        hand = player.hand
        ranks = {}
        for card in hand:
            rank = card[:-1]
            ranks[rank] = ranks.get(rank, 0) + 1
        
        newly_collected_ranks = []
        for rank, count in ranks.items():
            if count == 4:
                newly_collected_ranks.append(rank)
                player.collected_sets.append(rank)
        
        if newly_collected_ranks:
            player.hand = [card for card in hand if card[:-1] not in newly_collected_ranks]
            return True
        return False

    async def check_and_deal_if_needed(self, player_name):
        """Перевіряє, чи порожня рука гравця, і якщо так, видає йому карту з колоди."""
        player = self.players.get(player_name)
        if player and not player.hand and not self.deck.is_empty():
            new_card = self.deck.draw()[0]
            player.hand.append(new_card)
            await self.notify_all(f"У гравця {player_name} порожня рука. Автоматично взято карту з колоди.")
            return True
        return False

    async def next_turn(self):
        player_names = list(self.players.keys())
        self.current_turn_index = (self.current_turn_index + 1) % len(player_names)
        self.asking_player = player_names[self.current_turn_index]
        self.target_player = None
        self.asked_rank = None
        await self.notify_all(f"Хід переходить до гравця {self.asking_player}.")
        
        # Перевіряємо, чи має наступний гравець карти, щоб розпочати хід
        await self.check_and_deal_if_needed(self.asking_player)
        
        await self.notify_all_state()

    async def check_end_game(self):
        total_collected = sum(len(p.collected_sets) for p in self.players.values())
        if total_collected == 9:
            winner = max(self.players.values(), key=lambda p: len(p.collected_sets))
            for p in self.players.values():
                await p.websocket.send(json.dumps({'type': 'game_over', 'winner': winner.name}))
            self.game_started = False
            return True
        return False
    
    async def handle_ask_card(self, asking_player_name, target_player_name, card_rank):
        self.asking_player = asking_player_name
        self.target_player = target_player_name
        self.asked_rank = card_rank
        
        target_player = self.players.get(target_player_name)
        if target_player:
            message = {
                'type': 'ask_response_needed',
                'asking_player': asking_player_name,
                'card_rank': card_rank
            }
            await target_player.websocket.send(json.dumps(message))

    async def handle_ask_response(self, target_player_name, response):
        asking_player = self.players.get(self.asking_player)
        target_player = self.players.get(target_player_name)
        
        target_cards_to_transfer = [card for card in target_player.hand if card[:-1] == self.asked_rank]
        
        if response == 'yes':
            #if target_cards_to_transfer:
            await self.notify_all(f"Гравець {target_player.name} відповідає 'Так'.")
                #for card in target_cards_to_transfer:
                #    target_player.hand.remove(card)
                #    asking_player.hand.append(card)
                
                #await self.notify_all(f"Гравець {asking_player.name} отримує карти.")
                
                #if self.check_for_sets(asking_player):
                #    await self.notify_all(f"Гравець {asking_player.name} зібрав скриньку!")
                
                # Якщо у гравця, що відповів, не залишилось карт, він бере нову з колоди
                #await self.check_and_deal_if_needed(target_player_name)
                
                #await self.notify_all(f"Гравець {asking_player.name} продовжує свій хід.")

            #else:
            #     await self.notify_all(f"Гравець {target_player.name} помилився, у нього немає запитаної карти.")
            #     await self.draw_card_and_check_sets(asking_player, self.asked_rank)
            await self.notify_all(f"Гравець {self.asking_player} має вгадати кількість карт.")
            await self.players.get(self.asking_player).websocket.send(json.dumps({
                'type': 'guess_count_needed',
                'target_player': target_player_name,
                'card_rank': self.asked_rank
            }))
        
        else:
            await self.notify_all(f"Гравець {target_player.name} відповідає 'Ні'. {asking_player.name} іде на рибалку.")
            await self.draw_card_and_check_sets(asking_player)#, self.asked_rank)
        
        await self.check_end_game()
        await self.notify_all_state()

    async def draw_card_and_check_sets(self, player): 
        if not self.deck.is_empty():
            new_card = self.deck.draw()[0]
            player.hand.append(new_card)
            
            await self.notify_all(f"Гравець {player.name} бере карту з колоди.")
            
            if self.check_for_sets(player):
                await self.notify_all(f"Гравець {player.name} зібрав скриньку!")
            
            await self.next_turn()

    async def handle_guess_count(self, guessing_player_name, count):
        asking_player = self.players.get(guessing_player_name)
        target_player = self.players.get(self.target_player)

        # Визначаємо правильну кількість карт у суперника
        correct_count = sum(1 for card in target_player.hand if card[:-1] == self.asked_rank)
    
        # Додаємо запис в історію гри
        await self.notify_all(f"Гравець {asking_player.name} вгадує, що у гравця {target_player.name} {count} карт рангу {self.asked_rank}.")

        if count == correct_count:
            # Успішне вгадування кількості
            await self.notify_all(f"Гравець {asking_player.name} вгадав кількість карт: {count}. Він продовжує вгадувати масті.")

            # Відправляємо клієнту повідомлення з даними, необхідними для відображення форми
            await asking_player.websocket.send(json.dumps({
                'type': 'guess_suits_needed',
                'target_player': self.target_player,
                'card_rank': self.asked_rank,
                'correct_count': correct_count
            }))
        
            # Встановлюємо наступний крок
            self.current_step = 'guess_suits'

        else:
            # Невдале вгадування
            await self.notify_all(f"Гравець {asking_player.name} не вгадав кількість. Він бере карту з колоди.")
        
            # Далі продовжуємо гру, як і раніше
            await self.draw_card_and_check_sets(asking_player)

        await self.check_end_game()
        await self.notify_all_state()

    async def handle_guess_suits(self, asking_player_name, suits):
        asking_player = self.players.get(asking_player_name)
        target_player = self.players.get(self.target_player)
        
        target_cards_to_transfer = [card for card in target_player.hand if card[:-1] == self.asked_rank]
        target_suits = [card[-1] for card in target_cards_to_transfer]
        
        guessed_correctly = set(suits) == set(target_suits)

        if guessed_correctly:
            for card in target_cards_to_transfer:
                target_player.hand.remove(card)
                asking_player.hand.append(card)
            
            await self.notify_all(f"Гравець {asking_player.name} вгадав масті і отримує карти від гравця {target_player.name}.")
            
            self.check_for_sets(asking_player)

            # Якщо у гравця, що вгадав, не залишилось карт, він бере нову з колоди
            await self.check_and_deal_if_needed(asking_player.name)
            
            # Якщо у гравця, що відповів, не залишилось карт, він бере нову з колоди
            await self.check_and_deal_if_needed(target_player.name)
            
            await self.check_end_game()
            await self.notify_all_state()
            
            self.asking_player = asking_player_name
            self.target_player = None
            self.asked_rank = None
            await self.notify_all(f"Гравець {asking_player_name} продовжує свій хід.")

        else:
            await self.notify_all(f"Гравець {asking_player.name} не вгадав масті і бере карту з колоди.")
            await self.draw_card_and_check_sets(asking_player)  #, self.asked_rank)
            
        await self.check_end_game()
        await self.notify_all_state()


    def get_state(self):
        player_list = [{'name': p.name, 'is_turn': p.name == self.asking_player, 'collected_boxes': len(p.collected_sets), 'collected_sets': p.collected_sets} for p in self.players.values()]
        return {
            'game_started': self.game_started,
            'players': player_list,
            'deck_size': len(self.deck.cards),
            'current_turn': self.asking_player,
            'room_admin': self.room_admin
        }

    async def notify_all_state(self):
        state = self.get_state()
        for player in self.players.values():
            try:
                player_state = {**state, 'my_hand': player.hand}
                await player.websocket.send(json.dumps({'type': 'update_state', 'state': player_state}))
            except websockets.exceptions.ConnectionClosedError:
                logger.warning(f"Failed to send state update to {player.name}, connection closed.")


    async def notify_all(self, message):
        for player in self.players.values():
            try:
                await player.websocket.send(json.dumps({'type': 'log', 'message': message}))
            except websockets.exceptions.ConnectionClosedError:
                logger.warning(f"Failed to send log message to {player.name}, connection closed.")


game_rooms = {}

async def handler(websocket):
    player_name = None
    room_id = None
    try:
        async for message in websocket:
            data = json.loads(message)
            
            if data['type'] == 'join':
                player_name = data['name']
                room_id = data['room']

                if room_id not in game_rooms:
                    game_rooms[room_id] = Game()
                
                game = game_rooms[room_id]
                success, msg = await game.add_player(player_name, websocket)
                
                if success:
                    logger.info(f"Гравець {player_name} приєднався до кімнати {room_id}")
                    await websocket.send(json.dumps({'type': 'joined_room'}))
                    await game.notify_all(f"Гравець {player_name} приєднався до гри.")
                    await game.notify_all_state()
                else:
                    await websocket.send(json.dumps({'type': 'error', 'message': msg}))
            
            if player_name and room_id and room_id in game_rooms:
                game = game_rooms[room_id]
                if data['type'] == 'start_game' and player_name == game.room_admin:
                    if await game.start_game():
                        pass
                    else:
                        await websocket.send(json.dumps({'type': 'error', 'message': "Недостатньо гравців."}))
                
                elif data['type'] == 'ask_card' and player_name == game.asking_player:
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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server stopped by user")
