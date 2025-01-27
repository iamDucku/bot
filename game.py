import discord
from discord.ext import commands
import random
import asyncio
from collections import defaultdict
import json
import aiosqlite
import streamlit as st


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

DB_PATH = 'game_data.db'

class PlayerData:
    def __init__(self, balance=100, score=0, inventory=None):
        self.balance = balance
        self.score = score
        self.inventory = inventory or []

    @classmethod
    async def get(cls, player_id):
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('SELECT * FROM players WHERE id = ?', (player_id,))
            row = await cursor.fetchone()
            if row:
                return cls(balance=row[1], score=row[2], inventory=json.loads(row[3]))
            return cls()

    async def save(self, player_id):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('''
                INSERT OR REPLACE INTO players (id, balance, score, inventory)
                VALUES (?, ?, ?, ?)
            ''', (player_id, self.balance, self.score, json.dumps(self.inventory)))
            await db.commit()

shop_items = {
    "bomb detector": {"price": 500, "emoji": "🔍"},
    "lucky charm": {"price": 1000, "emoji": "🍀"},
    "safety net": {"price": 750, "emoji": "🥅"},
    "extra life": {"price": 2000, "emoji": "❤️"}
}

REACTIONS = [f"{i+1}\uFE0F\u20E3" for i in range(9)] + ['✅']

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    await init_db()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY,
                balance INTEGER,
                score INTEGER,
                inventory TEXT
            )
        ''')
        await db.commit()

@bot.command()
async def use(ctx):
    player_data = await PlayerData.get(ctx.author.id)
    
    if not player_data.inventory:
        await ctx.send("Your inventory is empty.")
        return

    embed = discord.Embed(title="Select an Item to Use", color=discord.Color.blue())
    
    item_counts = {}
    for item in player_data.inventory:
        if item not in item_counts:
            item_counts[item] = 1
        else:
            item_counts[item] += 1

    for item, count in item_counts.items():
        embed.add_field(
            name=f"{shop_items[item]['emoji']} {item.title()}", 
            value=f"Quantity: {count}", 
            inline=False
        )

    message = await ctx.send(embed=embed)

    available_reactions = {}
    for item in item_counts:
        emoji = shop_items[item]['emoji']
        await message.add_reaction(emoji)
        available_reactions[emoji] = item

    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in available_reactions

    try:
        reaction, _ = await bot.wait_for('reaction_add', timeout=30.0, check=check)
        selected_item = available_reactions[str(reaction.emoji)]
        
        player_data.inventory.remove(selected_item)
        await player_data.save(ctx.author.id)
        
        await ctx.send(f"You have activated {selected_item.title()} {shop_items[selected_item]['emoji']} for your next game.")
        
    except asyncio.TimeoutError:
        await ctx.send("Item selection timed out.")

@bot.command()
async def play(ctx, bet: int = 10, difficulty: str = "normal"):
    player_data = await PlayerData.get(ctx.author.id)
    
    if bet < 1:
        await ctx.send("Minimum bet is 1 coin.")
        return
    
    if player_data.balance < bet:
        await ctx.send("You don't have enough coins to place this bet!")
        return

    board = ['⬜'] * 9
    if difficulty.lower() == "easy":
        bombs = random.sample(range(9), 1)
        multiplier = 1.5
    elif difficulty.lower() == "hard":
        bombs = random.sample(range(9), 3)
        multiplier = 3
    else:  # normal
        bombs = random.sample(range(9), 2)
        multiplier = 2
    
    for bomb in bombs:
        board[bomb] = '💣'
    
    display_board_state = ['⬜'] * 9
    original_bet = bet

    active_items = player_data.inventory.copy()
    for item in active_items:
        if item == "bomb detector":
            if bombs:
                revealed_bomb = random.choice(bombs)
                display_board_state[revealed_bomb] = '💣'
                await ctx.send(f"Your Bomb Detector 🔍 revealed a bomb at position {revealed_bomb + 1}!")
        elif item == "lucky charm":
            multiplier *= 1.1
            await ctx.send(f"Your Lucky Charm 🍀 increased your multiplier by 10%!")
        
        if item in player_data.inventory:
            player_data.inventory.remove(item)
    
    await player_data.save(ctx.author.id)

    message = await ctx.send(f"Difficulty: {difficulty} (x{multiplier:.2f})\nCurrent bet: {bet} coins\nCurrent balance: {player_data.balance} coins\n\n{display_board(display_board_state)}")
    
    for reaction in REACTIONS:
        await message.add_reaction(reaction)
    
    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in REACTIONS
    
    picked_boxes = set()
    
    while True:
        try:
            reaction, user = await bot.wait_for('reaction_add', check=check, timeout=60.0)
            choice = str(reaction.emoji)
            
            if choice == '✅':
                winnings = int((bet - original_bet) * multiplier)
                player_data.balance += winnings
                player_data.score += winnings
                await player_data.save(ctx.author.id)
                await ctx.send(f"You've decided to leave the game. You keep your winnings of {winnings} coins.")
                break
            
            choice_index = REACTIONS.index(choice)
            
            if choice_index in picked_boxes:
                await ctx.send("You already picked this box! Choose another one.")
                await message.remove_reaction(reaction, user)
                continue
            
            picked_boxes.add(choice_index)
            
            if board[choice_index] == '💣':
                display_board_state[choice_index] = '💥'
                player_data.balance = max(0, player_data.balance - original_bet)
                player_data.score = max(0, player_data.score - original_bet)
                await player_data.save(ctx.author.id)
                await message.edit(content=f"You hit a bomb! You lose {original_bet} coins.\n\n{display_board(display_board_state)}")
                break
            else:
                display_board_state[choice_index] = '✅'
                bet *= 2
                await message.edit(content=f"Difficulty: {difficulty} (x{multiplier:.2f})\nSafe! Current bet: {bet} coins\nCurrent balance: {player_data.balance} coins\n\n{display_board(display_board_state)}")
            
            await message.remove_reaction(reaction, user)
            
            if len(picked_boxes) == 9 - len(bombs):
                winnings = int((bet - original_bet) * multiplier)
                player_data.balance += winnings
                player_data.score += winnings
                await player_data.save(ctx.author.id)
                await ctx.send(f"Congratulations! You've cleared all safe boxes! You win {winnings} coins!")
                break
        
        except asyncio.TimeoutError:
            await ctx.send("Game timed out.")
            break

@bot.command()
async def money(ctx):
    player_data = await PlayerData.get(ctx.author.id)
    await ctx.send(f"Your current balance is {player_data.balance:,} coins.")

@bot.command()
async def shop(ctx):
    embed = discord.Embed(title="Shop", description="Spend your coins on upgrades!", color=discord.Color.green())
    for item, details in shop_items.items():
        embed.add_field(
            name=f"{details['emoji']} {item.title()} ({details['price']:,} coins)", 
            value=f"Buy with `/buy {item}`", 
            inline=False
        )
    await ctx.send(embed=embed)

@bot.command()
async def buy(ctx, *, item: str):
    player_data = await PlayerData.get(ctx.author.id)
    item = item.lower()
    
    if item not in shop_items:
        await ctx.send("Invalid item. Use /shop to see available items.")
        return
    
    if player_data.balance < shop_items[item]["price"]:
        await ctx.send("You don't have enough coins to buy this item!")
        return
    
    player_data.balance -= shop_items[item]["price"]
    player_data.inventory.append(item)
    await player_data.save(ctx.author.id)
    await ctx.send(f"You've purchased {item.title()} {shop_items[item]['emoji']} for {shop_items[item]['price']:,} coins!")

@bot.command()
async def inventory(ctx):
    player_data = await PlayerData.get(ctx.author.id)
    if not player_data.inventory:
        await ctx.send("Your inventory is empty.")
        return

    item_counts = {}
    for item in player_data.inventory:
        item_counts[item] = item_counts.get(item, 0) + 1
    
    items_display = [f"{shop_items[item]['emoji']} {item.title()}: {count}" for item, count in item_counts.items()]
    await ctx.send("Your inventory:\n" + "\n".join(items_display))

@bot.command()
async def leaderboard(ctx):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('SELECT id, score FROM players ORDER BY score DESC LIMIT 10')
        top_10 = await cursor.fetchall()

    embed = discord.Embed(title="🏆 Top 10 Players", color=discord.Color.gold())
    
    for i, (player_id, score) in enumerate(top_10, 1):
        user = await bot.fetch_user(player_id)
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "👑"
        embed.add_field(
            name=f"{medal} #{i} - {user.name}", 
            value=f"Score: {score:,} coins", 
            inline=False
        )
    
    if not top_10:
        embed.add_field(
            name="No players yet!", 
            value="Be the first to play and get on the leaderboard!", 
            inline=False
        )
    
    await ctx.send(embed=embed)

def display_board(board):
    return '\n'.join([''.join(board[i:i+3]) for i in range(0, 9, 3)])


bot.run(st.secrets["DISCORD_TOKEN"])