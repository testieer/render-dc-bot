import discord
from discord.ext import commands
import os

# Replace with your bot token
TOKEN = os.getenv("DISCORD_TOKEN")

# Command prefix (change if you want)
bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user}")

# Example command
@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

# Example custom command
@bot.command()
async def hello(ctx):
    await ctx.send(f"Hello, {ctx.author.mention}!")

bot.run(TOKEN)
