import asyncio
import json
import random
import websockets
import os
import logging


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

    async def add_player(self, name, websocket):
        if not self.game_started and len(self.players) < 6:
            player = Player(name, websocket)
            self.players[name] = player
            return True, f"Гравець {name} приєднався."
        elif self.game_started:
            return False, "Гра вже розпочалась."
        else:
            return False, "Кімната повна."

    def remove_player(self, name):
        if name in self.players:
            del self.players[name]

    async def start_game(self):
        if len(self.players) >= 2 and not self.game_started:
            self.game_started = True
            self.deck = Deck()
            
            # Роздача по 4 карти
            for _ in range(4):
                for player_name in self.players:
                    card = self.deck.draw()[0]
                    self.players[player_name].hand.append(card)
            
            # Перша перевірка на скриньки
            for player_name in self.players:
                self.check_for_sets(self.players[player_name])

            await self.notify_all_state()
            return True
        return False
        
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
            # Видаляємо зібрані скриньки з руки гравця
            player.hand = [card for card in hand if card[:-1] not in newly_collected_ranks]
            return True
        return False

    def next_turn(self):
        player_names = list(self.players.keys())
        self.current_turn_index = (self.current_turn_index + 1) % len(player_names)
        self.asking_player = player_names[self.current_turn_index]
        self.target_player = None
        self.asked_rank = None

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
        
        if response == 'yes':
            count = sum(1 for card in target_player.hand if card[:-1] == self.asked_rank)
            await asking_player.websocket.send(json.dumps({
                'type': 'guess_count_needed', 
                'target_player': target_player_name,
                'card_rank': self.asked_rank,
                'count': count # Відправляємо кількість для перевірки
            }))
        else: # response == 'no'
            # Гравець бере карту з колоди
            if not self.deck.is_empty():
                new_card = self.deck.draw()[0]
                asking_player.hand.append(new_card)
                await self.notify_all(f"Гравець {asking_player.name} не вгадав і бере карту з колоди.")
                
                # Перевірка на скриньку
                if self.check_for_sets(asking_player):
                    await self.notify_all(f"Гравець {asking_player.name} зібрав скриньку!")
            
            # Якщо немає карт на руці, бере з колоди
            if not asking_player.hand and not self.deck.is_empty():
                 new_card = self.deck.draw()[0]
                 asking_player.hand.append(new_card)
                 await self.notify_all(f"Гравець {asking_player.name} залишився без карт і бере нову з колоди.")

            await self.check_end_game()
            self.next_turn()
            await self.notify_all_state()

    async def handle_guess_count(self, guessing_player_name, count):
        asking_player = self.players.get(guessing_player_name)
        target_player = self.players.get(self.target_player)
        
        correct_count = sum(1 for card in target_player.hand if card[:-1] == self.asked_rank)
        
        if count == correct_count:
            await asking_player.websocket.send(json.dumps({
                'type': 'guess_suits_needed'
            }))
            await self.notify_all(f"Гравець {asking_player.name} вгадав кількість карт: {count}. Він продовжує вгадувати масті.")
        else:
            if not self.deck.is_empty():
                new_card = self.deck.draw()[0]
                asking_player.hand.append(new_card)
                await self.notify_all(f"Гравець {asking_player.name} не вгадав кількість і бере карту з колоди.")

            if not asking_player.hand and not self.deck.is_empty():
                 new_card = self.deck.draw()[0]
                 asking_player.hand.append(new_card)
                 await self.notify_all(f"Гравець {asking_player.name} залишився без карт і бере нову з колоди.")

            await self.check_end_game()
            self.next_turn()
            await self.notify_all_state()

    async def handle_guess_suits(self, asking_player_name, suits):
        asking_player = self.players.get(asking_player_name)
        target_player = self.players.get(self.target_player)
        
        target_cards_to_transfer = [card for card in target_player.hand if card[:-1] == self.asked_rank]
        target_suits = [card[-1] for card in target_cards_to_transfer]
        
        guessed_correctly = set(suits) == set(target_suits)

        if guessed_correctly:
            # Передаємо карти
            for card in target_cards_to_transfer:
                target_player.hand.remove(card)
                asking_player.hand.append(card)
            await self.notify_all(f"Гравець {asking_player.name} вгадав масті і отримує карти від гравця {target_player.name}.")
            
            self.check_for_sets(asking_player)
            
            # Якщо у target_player не залишилося карт, він бере з колоди
            if not target_player.hand and not self.deck.is_empty():
                new_card = self.deck.draw()[0]
                target_player.hand.append(new_card)
                await self.notify_all(f"Гравець {target_player.name} залишився без карт і бере нову з колоди.")
            
            await self.check_end_game()
            await self.notify_all_state()
            
            # Хід залишається у того ж гравця
            self.asking_player = asking_player_name
            self.target_player = None
            self.asked_rank = None
            await self.notify_all(f"Гравець {asking_player_name} продовжує свій хід.")

        else:
            if not self.deck.is_empty():
                new_card = self.deck.draw()[0]
                asking_player.hand.append(new_card)
                await self.notify_all(f"Гравець {asking_player.name} не вгадав масті і бере карту з колоди.")
            
            if not asking_player.hand and not self.deck.is_empty():
                 new_card = self.deck.draw()[0]
                 asking_player.hand.append(new_card)
                 await self.notify_all(f"Гравець {asking_player.name} залишився без карт і бере нову з колоди.")
            
            await self.check_end_game()
            self.next_turn()
            await self.notify_all_state()

    def get_state(self):
        player_list = [{'name': p.name, 'is_turn': p.name == list(self.players.keys())[self.current_turn_index], 'collected_boxes': len(p.collected_sets), 'collected_sets': p.collected_sets} for p in self.players.values()]
        return {
            'game_started': self.game_started,
            'players': player_list,
            'deck_size': len(self.deck.cards),
            'current_turn': list(self.players.keys())[self.current_turn_index] if self.players else None
        }

    async def notify_all_state(self):
        state = self.get_state()
        for player in self.players.values():
            player_state = {**state, 'my_hand': player.hand}
            await player.websocket.send(json.dumps({'type': 'update_state', 'state': player_state}))

    async def notify_all(self, message):
        for player in self.players.values():
            await player.websocket.send(json.dumps({'type': 'log', 'message': message}))


game = Game()
game_rooms = {}  # Словник для зберігання об'єктів Game за ID кімнати

async def handler(websocket):
    player_name = None
    try:
        async for message in websocket:
            data = json.loads(message)
            
            if data['type'] == 'join':
                player_name = data['name']
                success, msg = await game.add_player(player_name, websocket)
                if success:
                    print(f"Новий гравець приєднався: {player_name}")
                    await game.notify_all(f"Гравець {player_name} приєднався до гри.")
                    await game.notify_all_state()
                else:
                    await websocket.send(json.dumps({'type': 'error', 'message': msg}))
            
            # Інші типи повідомлень обробляються лише від зареєстрованого гравця
            if player_name:
                if data['type'] == 'start_game':
                    if await game.start_game():
                        await game.notify_all("Гра розпочалась!")
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
        print(f"З'єднання закрито для гравця {player_name}")
    finally:
        if player_name:
            game.remove_player(player_name)
            await game.notify_all(f"Гравець {player_name} відключився.")
            if game.game_started and not game.players:
                game.game_started = False
                print("Всі гравці вийшли, гра зупинена.")
            await game.notify_all_state()

# ----------------------------
# Запуск сервера
# ----------------------------
async def main():
    port_env = os.environ.get("PORT")
    port = int(port_env) if port_env else 8765
    logging.info(f"Starting WebSocket server on 0.0.0.0:{port}")
    async with websockets.serve(handler, "0.0.0.0", port):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server stopped by user")
