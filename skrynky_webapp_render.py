#!/usr/bin/env python3
# skrynky_webapp_render.py
# Потрібні залежності:
# pip install websockets

import asyncio
import json
import websockets
import random
import logging
import os

logging.basicConfig(level=logging.INFO)

# ----------------------------
# Клас гри (скопійовано та адаптовано з вашого skrynky_GEMINI_2.py)
# ----------------------------
class SkrynkyGame:
    def __init__(self, players):
        self.players = players[:]  # list of names
        self.currentPlayerIdx = 0
        self.deck = self._generateDeck()
        self.hands = {name: [] for name in self.players}
        self.boxes = {name: 0 for name in self.players}
        self.usedBoxes = set()
        self.collectedBoxes = {name: [] for name in self.players}
        self.history = []

        self.addToHistory("system", "Гра розпочалася!")
        self.dealCards()
        self.update_player_state()

    def _generateDeck(self, size=36):
        ranks = ['6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A'][:size // 4]
        suits = ['♥', '♦', '♣', '♠']
        deck = [f"{rank}{suit}" for rank in ranks for suit in suits]
        random.shuffle(deck)
        return deck

    def _get_rank(self, card):
        if card.startswith('10'):
            return '10'
        return card[0]

    def dealCards(self):
        for _ in range(4):
            for player in self.players:
                if self.deck:
                    card = self.deck.pop()
                    self.hands[player].append(card)
                    self._checkBoxesAfterDeal(player, self._get_rank(card))
        self.addToHistory("system", "Роздано початкові карти гравцям")

    def _checkBoxesAfterDeal(self, player, rank):
        if rank in self.usedBoxes: return
        cards = [c for c in self.hands[player] if self._get_rank(c) == rank]
        if len(cards) == 4:
            self.boxes[player] += 1
            self.usedBoxes.add(rank)
            self.collectedBoxes[player].append(rank)
            self.hands[player] = [c for c in self.hands[player] if self._get_rank(c) != rank]
            self.addToHistory("system", f"⚡️ {player} зібрав скриньку {rank}!")
            if not self.hands[player] and self.deck:
                self.drawCard(player)
            self.update_player_state()

    def checkBoxes(self, player):
        changed = False
        ranks_in_hand = {self._get_rank(card) for card in self.hands[player]}
        for rank in ranks_in_hand:
            if rank not in self.usedBoxes and sum(1 for c in self.hands[player] if self._get_rank(c) == rank) == 4:
                self.boxes[player] += 1
                self.usedBoxes.add(rank)
                self.collectedBoxes[player].append(rank)
                self.hands[player] = [c for c in self.hands[player] if self._get_rank(c) != rank]
                self.addToHistory("system", f"⚡️ {player} зібрав скриньку {rank}!")
                changed = True
        if not self.hands[player] and self.deck:
            self.drawCard(player)
        self.update_player_state()
        return changed

    def drawCard(self, player):
        if not self.deck:
            return False
        card = self.deck.pop()
        self.hands[player].append(card)
        self.addToHistory("system", f"🃏 {player} взяв карту з колоди.")
        self._checkBoxesAfterDeal(player, self._get_rank(card))
        self.update_player_state()
        return True

    def takeCards(self, from_player, cards_to_take):
        taken_cards = []
        for card in cards_to_take:
            if card in self.hands[from_player]:
                self.hands[from_player].remove(card)
                taken_cards.append(card)
        return taken_cards

    def isGameOver(self):
        if len(self.usedBoxes) == 9:
            return True
        active_players = [p for p in self.players if self.hands[p] or self.deck]
        if len(active_players) <= 1:
            return True
        return False

    def get_winner(self):
        if not self.boxes: return None
        return max(self.boxes, key=self.boxes.get)

    def get_winners(self):
        if not self.boxes:
            return []
        max_boxes = max(self.boxes.values())
        return [player for player, boxes in self.boxes.items() if boxes == max_boxes]

    def getCurrentPlayer(self):
        if not self.players:
            return None
        return self.players[self.currentPlayerIdx]

    def nextTurn(self):
        if not self.players:
            return
        self.currentPlayerIdx = (self.currentPlayerIdx + 1) % len(self.players)
        player = self.players[self.currentPlayerIdx]
        attempts = 0
        while not self.hands[player] and self.deck and attempts < len(self.players) * 2:
            self.addToHistory("system", f"{player} не має карт. Передача ходу.")
            self.drawCard(player)
            self.currentPlayerIdx = (self.currentPlayerIdx + 1) % len(self.players)
            player = self.players[self.currentPlayerIdx]
            attempts += 1

    def addToHistory(self, type, message):
        from datetime import datetime
        timestamp = datetime.utcnow().isoformat() + "Z"
        self.history.append({'type': type, 'message': message, 'timestamp': timestamp})

    def update_player_state(self):
        for player in self.players:
            self.hands[player].sort()

    def get_game_state(self):
        return {
            "players": self.players,
            "currentPlayer": self.getCurrentPlayer(),
            "deckCount": len(self.deck),
            "hands": self.hands,
            "boxes": self.boxes,
            "collectedBoxes": self.collectedBoxes,
            "history": self.history
        }

# ----------------------------
# Rooms management & WS handler
# ----------------------------
game_rooms = {}
next_room_id = 1

async def handler(websocket, path):
    player_name = None
    room_id = None
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except Exception:
                await websocket.send(json.dumps({"type": "error", "message": "Невірний JSON"}))
                continue

            action = data.get("action")
            # --- JOIN ---
            if action == "join":
                player_name = data.get("name") or f"User_{random.randint(1000,9999)}"
                tg_id = data.get("tg_id")
                tg_name = data.get("tg_name")
                room_provided = data.get("room_id")

                global next_room_id
                if room_provided:
                    room_id = int(room_provided)
                    if room_id in game_rooms and len(game_rooms[room_id]["players"]) < 6:
                        game_rooms[room_id]["players"][player_name] = {"ws": websocket, "tg_id": tg_id, "tg_name": tg_name}
                        await notify_players(room_id, {"type": "info", "message": f"{player_name} приєднався до кімнати."})
                    else:
                        await websocket.send(json.dumps({"type": "error", "message": "Кімната не існує або вже повна."}))
                        continue
                else:
                    room_id = next_room_id
                    while room_id in game_rooms:
                        room_id += 1
                    game_rooms[room_id] = {"players": {player_name: {"ws": websocket, "tg_id": tg_id, "tg_name": tg_name}}, "game": None}
                    next_room_id = room_id + 1
                    await websocket.send(json.dumps({"type": "room_created", "room_id": room_id}))

                await notify_players(room_id, get_room_state(room_id))

            # --- START GAME ---
            elif action == "start_game" and room_id in game_rooms and game_rooms[room_id]["game"] is None:
                players_in_room = list(game_rooms[room_id]["players"].keys())
                if len(players_in_room) >= 2:
                    game = SkrynkyGame(players_in_room)
                    game_rooms[room_id]["game"] = game
                    await notify_players(room_id, {"type": "game_started", "state": game.get_game_state()})
                else:
                    await websocket.send(json.dumps({"type": "error", "message": "Потрібно мінімум 2 гравці."}))

            # --- MAKE TURN ---
            elif action == "make_turn" and room_id in game_rooms and game_rooms[room_id]["game"] is not None:
                game = game_rooms[room_id]["game"]
                if not player_name or player_name != game.getCurrentPlayer():
                    await websocket.send(json.dumps({"type": "error", "message": "Зараз не ваш хід."}))
                    continue

                step = data.get("step")
                # ask_rank
                if step == "ask_rank":
                    opponent = data.get("opponent")
                    rank = data.get("rank")
                    game.addToHistory("turn", f"➡ {player_name} запитує у {opponent} карти номіналу \"{rank}\"")
                    opponent_cards = [c for c in game.hands.get(opponent, []) if game._get_rank(c) == rank]
                    if opponent_cards:
                        game.addToHistory("system", f"✅ У {opponent} є карти номіналу \"{rank}\".")
                        await notify_players(room_id, {"type": "next_step", "step": "guess_count", "opponent": opponent, "rank": rank, "count": len(opponent_cards), "state": game.get_game_state()})
                    else:
                        game.addToHistory("system", f"❌ У {opponent} немає карт номіналу \"{rank}\".")
                        game.drawCard(player_name)
                        game.nextTurn()
                        await notify_players(room_id, {"type": "state_update", "state": game.get_game_state()})

                # guess_count
                elif step == "guess_count":
                    opponent = data.get("opponent")
                    rank = data.get("rank")
                    guess = data.get("guess")
                    game.addToHistory("turn", f"➡ {player_name} вгадує, що у {opponent} {guess} карт номіналу \"{rank}\"")
                    correct_count = len([c for c in game.hands.get(opponent, []) if game._get_rank(c) == rank])
                    if guess == correct_count:
                        game.addToHistory("system", "✅ Кількість вгадана правильно!")
                        await notify_players(room_id, {"type": "next_step", "step": "guess_suits", "opponent": opponent, "rank": rank, "count": guess, "state": game.get_game_state()})
                    else:
                        game.addToHistory("system", f"❌ Кількість вгадана неправильно.")
                        game.drawCard(player_name)
                        game.nextTurn()
                        await notify_players(room_id, {"type": "state_update", "state": game.get_game_state()})

                # guess_suits
                elif step == "guess_suits":
                    opponent = data.get("opponent")
                    rank = data.get("rank")
                    guessed_suits = data.get("suits", [])
                    guessed_cards = [f"{rank}{s}" for s in guessed_suits]
                    opponent_cards = [c for c in game.hands.get(opponent, []) if game._get_rank(c) == rank]
                    if sorted(guessed_cards) == sorted(opponent_cards):
                        game.addToHistory("system", "✅ Масті вгадані правильно!")
                        taken_cards = game.takeCards(opponent, opponent_cards)
                        game.hands[player_name].extend(taken_cards)
                        game.checkBoxes(player_name)
                        game.addToHistory("system", f"⚡️ {player_name} забрав карти {', '.join(taken_cards)} у {opponent}! Хід продовжується.")
                        if not game.hands[opponent] and game.deck:
                            game.drawCard(opponent)
                        if not game.hands[player_name] and game.deck:
                            game.drawCard(player_name)
                        if game.isGameOver():
                            winners = game.get_winners()
                            await notify_players(room_id, {"type": "game_over", "state": game.get_game_state(), "winners": winners})
                        else:
                            await notify_players(room_id, {"type": "state_update", "state": game.get_game_state()})
                    else:
                        game.addToHistory("system", "❌ Масті вгадані неправильно.")
                        game.drawCard(player_name)
                        game.nextTurn()
                        await notify_players(room_id, {"type": "state_update", "state": game.get_game_state()})

    except websockets.exceptions.ConnectionClosed as e:
        logging.info(f"Connection closed by {player_name} in room {room_id} code={getattr(e,'code',None)}")
    finally:
        # cleanup
        try:
            if player_name and room_id in game_rooms and player_name in game_rooms[room_id]["players"]:
                del game_rooms[room_id]["players"][player_name]
                await notify_players(room_id, {"type": "info", "message": f"{player_name} вийшов з кімнати."})
                if not game_rooms[room_id]["players"]:
                    del game_rooms[room_id]
                    logging.info(f"Room {room_id} closed.")
        except Exception as ex:
            logging.warning(f"Cleanup error: {ex}")

async def notify_players(room_id, message):
    if room_id not in game_rooms:
        return
    game = game_rooms[room_id].get("game")
    for pname, pdata in list(game_rooms[room_id]["players"].items()):
        ws = pdata["ws"]
        try:
            if game:
                player_message = {**message}
                player_message["hand"] = game.hands.get(pname, [])
                player_message["is_current_player"] = (pname == game.getCurrentPlayer())
                await ws.send(json.dumps(player_message))
            else:
                await ws.send(json.dumps(message))
        except Exception as e:
            logging.warning(f"Error sending to {pname}: {e}")

def get_room_state(room_id):
    if room_id in game_rooms:
        players = list(game_rooms[room_id]["players"].keys())
        game = game_rooms[room_id].get("game")
        state = game.get_game_state() if game else {"players": players}
        return {"type": "room_state", "players": players, "state": state, "game_started": game is not None}
    return {}

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
