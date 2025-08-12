import asyncio
import json
import logging
import random
import uuid
import datetime
import websockets

# Налаштування логування
logging.basicConfig(level=logging.INFO)

# Конфігурація гри
SUITS = ["♥", "♦", "♣", "♠"]
RANKS = ["6", "7", "8", "9", "10", "J", "Q", "K", "A"]
DECK = [r + s for r in RANKS for s in SUITS]

# Зберігання стану гри
game_rooms = {}
websocket_to_player = {}

class SkrynkyGame:
    def __init__(self, room_id, players):
        self.room_id = room_id
        self.players = players
        self.deck = DECK.copy()
        self.hands = {p: [] for p in players}
        self.boxes = {p: 0 for p in players}
        self.collectedBoxes = {p: [] for p in players}
        self.current_player_index = 0
        self.history = []

        random.shuffle(self.deck)
        self.deal_cards()

        self.add_to_history("system", "Гра розпочалася! Роздано початкові карти гравцям.")

    def deal_cards(self):
        for _ in range(5):
            for player in self.players:
                if self.deck:
                    card = self.deck.pop(0)
                    self.hands[player].append(card)

    def is_game_over(self):
        return not self.deck and all(not hand for hand in self.hands.values())

    def get_game_state(self):
        return {
            "players": self.players,
            "hands": self.hands,
            "boxes": self.boxes,
            "collectedBoxes": self.collectedBoxes,
            "currentPlayer": self.players[self.current_player_index],
            "deckCount": len(self.deck),
            "history": self.history,
        }

    def get_card_rank(self, card):
        return card[:-1]

    def check_boxes(self, player_name):
        ranks = {}
        for card in self.hands[player_name]:
            rank = self.get_card_rank(card)
            if rank in ranks:
                ranks[rank].append(card)
            else:
                ranks[rank] = [card]

        for rank, cards in list(ranks.items()):
            if len(cards) == 4:
                self.boxes[player_name] += 1
                self.collectedBoxes[player_name].append(rank)
                self.hands[player_name] = [c for c in self.hands[player_name] if self.get_card_rank(c) != rank]
                self.add_to_history("system", f"⚡️ {player_name} зібрав скриньку {rank}!")
                
    def get_winners(self):
        if not self.boxes:
            return []
        max_boxes = max(self.boxes.values())
        return [player for player, boxes in self.boxes.items() if boxes == max_boxes]

    def next_turn(self):
        self.current_player_index = (self.current_player_index + 1) % len(self.players)

    def add_to_history(self, player, message):
        self.history.append({"player": player, "message": message, "timestamp": datetime.datetime.now(datetime.UTC).isoformat()})
    
    def take_cards(self, opponent, cards):
        taken_cards = [c for c in self.hands[opponent] if c in cards]
        self.hands[opponent] = [c for c in self.hands[opponent] if c not in taken_cards]
        return taken_cards
        
    def draw_card(self, player):
        if self.deck:
            card = self.deck.pop(0)
            self.hands[player].append(card)
            self.add_to_history("system", f"⚡️ {player} взяв карту з колоди.")

async def notify_players(room_id, message):
    if room_id in game_rooms:
        game = game_rooms[room_id]["game"]
        players = list(game_rooms[room_id]["players"].keys())
        for player_name in players:
            websocket = game_rooms[room_id]["players"][player_name]
            try:
                if game:
                    player_message = {**message}
                    player_message["hand"] = game.hands.get(player_name, [])
                    await websocket.send(json.dumps(player_message))
                else:
                    await websocket.send(json.dumps(message))
            except websockets.exceptions.ConnectionClosed:
                logging.warning(f"Connection to {player_name} in room {room_id} lost. Removing player.")
                del game_rooms[room_id]["players"][player_name]

async def handler(websocket, path):
    player_name = None
    room_id = None
    
    try:
        async for message in websocket:
            data = json.loads(message)
            action = data.get("action")

            if action == "join":
                player_name = data.get("name")
                room_provided = data.get("room_id")

                if room_provided:
                    room_id = room_provided
                else:
                    room_id = str(uuid.uuid4())[:8]

                if room_id not in game_rooms:
                    game_rooms[room_id] = {"players": {}, "game": None}

                game_rooms[room_id]["players"][player_name] = websocket
                websocket_to_player[websocket] = {"name": player_name, "room": room_id}
                
                await websocket.send(json.dumps({"type": "room_created", "room_id": room_id}))
                
                players_in_room = list(game_rooms[room_id]["players"].keys())
                await notify_players(room_id, {"type": "room_state", "players": players_in_room, "room_id": room_id})

            elif action == "start_game":
                if room_id in game_rooms and not game_rooms[room_id]["game"]:
                    players_in_room = list(game_rooms[room_id]["players"].keys())
                    if len(players_in_room) >= 2:
                        game = SkrynkyGame(room_id, players_in_room)
                        game_rooms[room_id]["game"] = game
                        await notify_players(room_id, {"type": "game_started", "state": game.get_game_state()})

            elif action == "check_opponent_cards":
                game = game_rooms.get(room_id, {}).get("game")
                if not game or game.players[game.current_player_index] != player_name:
                    continue
                
                opponent = data["opponent"]
                rank = data["rank"]
                
                opponent_cards_with_rank = [c for c in game.hands.get(opponent, []) if game.get_card_rank(c) == rank]
                count = len(opponent_cards_with_rank)
                
                if count > 0:
                    await websocket.send(json.dumps({"type": "count_options", "counts": [count]}))
                else:
                    game.add_to_history("system", f"❌ У {opponent} немає карт номіналу {rank}.")
                    game.draw_card(player_name)
                    game.check_boxes(player_name)
                    game.next_turn()
                    await notify_players(room_id, {"type": "next_step", "state": game.get_game_state()})

            elif action == "check_opponent_suits":
                game = game_rooms.get(room_id, {}).get("game")
                if not game or game.players[game.current_player_index] != player_name:
                    continue

                opponent = data["opponent"]
                rank = data["rank"]
                count = data["count"]
                
                opponent_cards_with_rank = [c for c in game.hands.get(opponent, []) if game.get_card_rank(c) == rank]
                if len(opponent_cards_with_rank) == count:
                    suits = [c[-1] for c in opponent_cards_with_rank]
                    await websocket.send(json.dumps({"type": "suit_options", "suits": suits}))
                else:
                    await websocket.send(json.dumps({"type": "error", "message": "Кількість карт не відповідає."}))

            elif action == "submit_request":
                game = game_rooms.get(room_id, {}).get("game")
                if not game or game.players[game.current_player_index] != player_name:
                    continue

                opponent = data["opponent"]
                rank = data["rank"]
                count = data["count"]
                guessed_suits = data["suits"]
                
                opponent_cards = [c for c in game.hands.get(opponent, []) if game.get_card_rank(c) == rank]
                
                if len(opponent_cards) == count:
                    if sorted([c[-1] for c in opponent_cards]) == sorted(guessed_suits):
                        game.add_to_history("system", f"✅ {player_name} вгадав карти {rank} у {opponent}!")
                        
                        taken_cards = game.take_cards(opponent, opponent_cards)
                        game.hands[player_name].extend(taken_cards)
                        
                        game.check_boxes(player_name)
                        
                        game.add_to_history("system", f"⚡️ {player_name} забрав карти {', '.join(taken_cards)}! Хід продовжується.")

                        if not game.hands[opponent] and game.deck:
                            game.draw_card(opponent)
                        
                        if not game.hands[player_name] and game.deck:
                            game.draw_card(player_name)

                        if game.is_game_over():
                            winners = game.get_winners()
                            await notify_players(room_id, {"type": "game_over", "state": game.get_game_state(), "winners": winners})
                        else:
                            await notify_players(room_id, {"type": "next_step", "state": game.get_game_state()})
                    else:
                        game.add_to_history("system", f"❌ {player_name} не вгадав масті карт {rank} у {opponent}.")
                        game.draw_card(player_name)
                        game.next_turn()
                        await notify_players(room_id, {"type": "next_step", "state": game.get_game_state()})
                else:
                    game.add_to_history("system", f"❌ {player_name} не вгадав кількість карт {rank} у {opponent}.")
                    game.draw_card(player_name)
                    game.next_turn()
                    await notify_players(room_id, {"type": "next_step", "state": game.get_game_state()})
            
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        if websocket in websocket_to_player:
            player_info = websocket_to_player[websocket]
            pname = player_info["name"]
            rid = player_info["room"]
            
            if rid in game_rooms and pname in game_rooms[rid]["players"]:
                del game_rooms[rid]["players"][pname]
                logging.info(f"Player {pname} left room {rid}")

            if rid in game_rooms and not game_rooms[rid]["players"]:
                del game_rooms[rid]
                logging.info(f"Room {rid} is empty and was deleted.")

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
