# main.py
import discord
from discord.ext import commands
import os
import asyncio
from datetime import datetime, timedelta
import re
import database  # local module
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Set DISCORD_TOKEN in env (Render secret)")

# Config
SUPER_COOL_ROLE = "super cool guy"  # case-insensitive check
OVERRIDE_PASSWORD = "ihategamingfof321"
MUTED_ROLE_NAME = "Muted"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# in-memory override trackers (user id -> True means next privileged command consumes it)
override_active = set()

# Helper: parse durations like 1s 30m 2h 3d 1w 1y
def parse_duration_to_seconds(s: str):
    if not s:
        return None
    match = re.fullmatch(r"(\d+)([smhdwy])", s.strip().lower())
    if not match:
        return None
    num = int(match.group(1))
    unit = match.group(2)
    mul = {"s":1, "m":60, "h":3600, "d":86400, "w":604800, "y":31536000}
    return num * mul[unit]

# DB init on start
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user} (id {bot.user.id})")
    await database.init_db()
    # schedule tasks for temp actions
    await schedule_pending_temp_actions()
    print("Startup tasks scheduled.")

def has_super_role(member: discord.Member):
    for r in member.roles:
        if (r.name or "").lower() == SUPER_COOL_ROLE.lower():
            return True
    return False

async def is_authorized_and_consume_override(ctx: commands.Context):
    # Owner or role holder allowed normally
    if ctx.guild is None:
        return False
    if ctx.author.id == ctx.guild.owner_id:
        return True
    if has_super_role(ctx.author):
        return True
    # override: if present for this user, consume and allow
    if ctx.author.id in override_active:
        override_active.discard(ctx.author.id)
        return True
    return False

# Create or get Muted role and ensure channel perms block sending/speaking
async def ensure_muted_role(guild: discord.Guild):
    role = discord.utils.get(guild.roles, name=MUTED_ROLE_NAME)
    if role:
        return role
    perms = discord.Permissions(send_messages=False, speak=False)
    role = await guild.create_role(name=MUTED_ROLE_NAME, permissions=discord.Permissions.none(), reason="Create muted role for bot")
    # apply channel overwrites
    for ch in guild.channels:
        try:
            await ch.set_permissions(role, speak=False, send_messages=False, add_reactions=False)
        except Exception:
            pass
    return role

# -------------------- override command --------------------
@bot.command(name="override")
async def cmd_override(ctx: commands.Context, password: str = None):
    if ctx.guild is None:
        return await ctx.send("Override works in servers only.")
    # only owner or role holder can enable override for themselves
    if ctx.author.id != ctx.guild.owner_id and not has_super_role(ctx.author):
        return await ctx.send("❌ Only the server owner or users with the role 'super cool guy' can enable override.")
    if password != OVERRIDE_PASSWORD:
        return await ctx.send("❌ Incorrect override password.")
    override_active.add(ctx.author.id)
    await ctx.send(f"🚨 Override ENABLED for {ctx.author.mention} — your **next** privileged command will run with admin privileges. It will auto-disable after one use.")

# -------------------- BAN/UNBAN --------------------
@bot.command(name="ban")
async def cmd_ban(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    if not await is_authorized_and_consume_override(ctx):
        return await ctx.send("⛔ You don't have permission. Need role 'super cool guy' or owner or an active override.")
    # owner cannot be targeted
    if member.id == ctx.guild.owner_id:
        return await ctx.send("❌ You cannot target the server owner.")
    # bot permission check
    if not ctx.guild.me.guild_permissions.ban_members:
        return await ctx.send("❌ I lack Ban Members permission or my role is too low.")
    try:
        await member.ban(reason=reason)
    except discord.Forbidden:
        return await ctx.send("❌ I can't ban that user (hierarchy).")
    await database.add_note(ctx.guild.id, member.id, f"BAN: {reason}", str(ctx.author))
    await ctx.send(f"🔨 {member} banned. Reason: {reason}")

@bot.command(name="unban")
async def cmd_unban(ctx: commands.Context, user: discord.User, *, reason: str = "No reason provided"):
    if not await is_authorized_and_consume_override(ctx):
        return await ctx.send("⛔ You don't have permission.")
    # find ban entry and unban
    try:
        bans = await ctx.guild.bans()
    except discord.Forbidden:
        return await ctx.send("❌ I lack permission to view bans.")
    for ban_entry in bans:
        if ban_entry.user.id == user.id:
            try:
                await ctx.guild.unban(ban_entry.user, reason=reason)
                await database.add_note(ctx.guild.id, user.id, f"UNBAN: {reason}", str(ctx.author))
                return await ctx.send(f"♻️ {user} has been unbanned. Reason: {reason}")
            except discord.Forbidden:
                return await ctx.send("❌ I cannot unban due to role hierarchy or missing perms.")
    await ctx.send("❌ That user is not banned on this server.")

# -------------------- TEMPBAN --------------------
@bot.command(name="tempban")
async def cmd_tempban(ctx: commands.Context, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    if not await is_authorized_and_consume_override(ctx):
        return await ctx.send("⛔ You don't have permission.")
    if member.id == ctx.guild.owner_id:
        return await ctx.send("❌ Cannot target the owner.")
    secs = parse_duration_to_seconds(duration)
    if secs is None:
        return await ctx.send("❌ Invalid duration. Use e.g. 30s 5m 2h 3d 1w 1y")
    if not ctx.guild.me.guild_permissions.ban_members:
        return await ctx.send("❌ I lack Ban Members permission.")
    try:
        await member.ban(reason=reason)
    except discord.Forbidden:
        return await ctx.send("❌ I cannot ban that user (hierarchy).")
    expires_iso = (datetime.utcnow() + timedelta(seconds=secs)).isoformat()
    await database.add_temp_action(ctx.guild.id, member.id, "tempban", reason, str(ctx.author), expires_iso)
    await database.add_note(ctx.guild.id, member.id, f"TEMPBAN({duration}): {reason}", str(ctx.author))
    # schedule unban
    bot.loop.create_task(schedule_unban(ctx.guild.id, member.id, secs))
    await ctx.send(f"⏳ {member} temp-banned for {duration}. Reason: {reason}")

async def schedule_unban(guild_id:int, user_id:int, secs:float):
    await asyncio.sleep(max(0, secs))
    guild = bot.get_guild(guild_id)
    if not guild:
        # couldn't find guild (bot removed?) — remove record anyway
        # cleanup DB: remove matching temp_actions rows for this user/guild
        rows = await database.get_all_temp_actions()
        for r in rows:
            if r[1] == guild_id and r[2] == user_id and r[3] == "tempban":
                await database.remove_temp_action_by_id(r[0])
        return
    try:
        await guild.unban(discord.Object(id=user_id), reason="Tempban expired")
    except Exception:
        pass
    # remove DB entry(s)
    rows = await database.get_all_temp_actions()
    for r in rows:
        if r[1] == guild_id and r[2] == user_id and r[3] == "tempban":
            await database.remove_temp_action_by_id(r[0])
    # announce in system channel if possible
    try:
        ch = guild.system_channel or next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)
        if ch:
            await ch.send(f"User <@{user_id}> has been unbanned (tempban expired).")
    except Exception:
        pass

# -------------------- MUTE/UNMUTE/TEMPMUTE --------------------
@bot.command(name="mute")
async def cmd_mute(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    if not await is_authorized_and_consume_override(ctx):
        return await ctx.send("⛔ No permission.")
    if member.id == ctx.guild.owner_id:
        return await ctx.send("❌ Cannot target the owner.")
    role = await ensure_muted_role(ctx.guild)
    try:
        await member.add_roles(role, reason=reason)
    except discord.Forbidden:
        return await ctx.send("❌ Can't add role (hierarchy).")
    await database.add_note(ctx.guild.id, member.id, f"MUTE: {reason}", str(ctx.author))
    await ctx.send(f"🔇 {member} muted. Reason: {reason}")

@bot.command(name="unmute")
async def cmd_unmute(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    if not await is_authorized_and_consume_override(ctx):
        return await ctx.send("⛔ No permission.")
    role = discord.utils.get(ctx.guild.roles, name=MUTED_ROLE_NAME)
    if role and role in member.roles:
        try:
            await member.remove_roles(role, reason=reason)
        except discord.Forbidden:
            return await ctx.send("❌ Can't remove role (hierarchy).")
        await database.add_note(ctx.guild.id, member.id, f"UNMUTE: {reason}", str(ctx.author))
        await ctx.send(f"🔊 {member} unmuted.")
    else:
        await ctx.send("❌ Member is not muted.")

@bot.command(name="tempmute")
async def cmd_tempmute(ctx: commands.Context, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    if not await is_authorized_and_consume_override(ctx):
        return await ctx.send("⛔ No permission.")
    if member.id == ctx.guild.owner_id:
        return await ctx.send("❌ Cannot target the owner.")
    secs = parse_duration_to_seconds(duration)
    if secs is None:
        return await ctx.send("❌ Invalid duration format.")
    role = await ensure_muted_role(ctx.guild)
    try:
        await member.add_roles(role, reason=reason)
    except discord.Forbidden:
        return await ctx.send("❌ Can't add role (hierarchy).")
    expires_iso = (datetime.utcnow() + timedelta(seconds=secs)).isoformat()
    await database.add_temp_action(ctx.guild.id, member.id, "tempmute", reason, str(ctx.author), expires_iso)
    await database.add_note(ctx.guild.id, member.id, f"TEMPMUTE({duration}): {reason}", str(ctx.author))
    bot.loop.create_task(schedule_unmute(ctx.guild.id, member.id, secs))
    await ctx.send(f"⏳ {member} temp-muted for {duration}. Reason: {reason}")

async def schedule_unmute(guild_id:int, user_id:int, secs:float):
    await asyncio.sleep(max(0, secs))
    guild = bot.get_guild(guild_id)
    if not guild:
        # cleanup DB
        rows = await database.get_all_temp_actions()
        for r in rows:
            if r[1] == guild_id and r[2] == user_id and r[3] == "tempmute":
                await database.remove_temp_action_by_id(r[0])
        return
    role = discord.utils.get(guild.roles, name=MUTED_ROLE_NAME)
    member = guild.get_member(user_id)
    if member and role and role in member.roles:
        try:
            await member.remove_roles(role, reason="Tempmute expired")
        except Exception:
            pass
    # remove DB record
    rows = await database.get_all_temp_actions()
    for r in rows:
        if r[1] == guild_id and r[2] == user_id and r[3] == "tempmute":
            await database.remove_temp_action_by_id(r[0])
    try:
        ch = guild.system_channel or next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)
        if ch:
            await ch.send(f"User <@{user_id}> has been unmuted (tempmute expired).")
    except Exception:
        pass

# -------------------- WARN / UNWARN / TEMPWARN --------------------
@bot.command(name="warn")
async def cmd_warn(ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
    if not await is_authorized_and_consume_override(ctx):
        return await ctx.send("⛔ No permission.")
    if member.id == ctx.guild.owner_id:
        return await ctx.send("❌ Cannot target the owner.")
    await database.add_warning(ctx.guild.id, member.id, reason, str(ctx.author), expires_at=None)
    await ctx.send(f"⚠️ {member.mention} warned. Reason: {reason}")

@bot.command(name="unwarn")
async def cmd_unwarn(ctx: commands.Context, member: discord.Member):
    if not await is_authorized_and_consume_override(ctx):
        return await ctx.send("⛔ No permission.")
    await database.remove_warnings_all(ctx.guild.id, member.id)
    await ctx.send(f"✅ All warnings removed for {member.mention}.")

@bot.command(name="tempwarn")
async def cmd_tempwarn(ctx: commands.Context, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    if not await is_authorized_and_consume_override(ctx):
        return await ctx.send("⛔ No permission.")
    if member.id == ctx.guild.owner_id:
        return await ctx.send("❌ Cannot target the owner.")
    secs = parse_duration_to_seconds(duration)
    if secs is None:
        return await ctx.send("❌ Invalid duration.")
    expires_iso = (datetime.utcnow() + timedelta(seconds=secs)).isoformat()
    await database.add_warning(ctx.guild.id, member.id, reason, str(ctx.author), expires_at=expires_iso)
    await database.add_temp_action(ctx.guild.id, member.id, "tempwarn", reason, str(ctx.author), expires_iso)
    bot.loop.create_task(schedule_tempwarn_expire(ctx.guild.id, member.id, secs))
    await ctx.send(f"⏳ {member.mention} temp-warned for {duration}. Reason: {reason}")

async def schedule_tempwarn_expire(guild_id:int, user_id:int, secs:float):
    await asyncio.sleep(max(0, secs))
    # remove expired tempwarn(s)
    rows = await database.get_all_temp_actions()
    for r in rows:
        if r[1] == guild_id and r[2] == user_id and r[3] == "tempwarn":
            await database.remove_temp_action_by_id(r[0])
    # remove expired warnings
    # database.remove expired handled in DB query or manual pruning: here we remove warnings with expires <= now
    # simple approach: remove warnings for this user where expires_at <= now
    async with __import__("aiosqlite").connect(database.DB_FILE) as db:
        await db.execute("DELETE FROM warnings WHERE guild_id=? AND user_id=? AND expires_at IS NOT NULL AND expires_at<=?",
                         (guild_id, user_id, datetime.utcnow().isoformat()))
        await db.commit()
    guild = bot.get_guild(guild_id)
    if guild:
        try:
            ch = guild.system_channel or next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)
            if ch:
                await ch.send(f"Tempwarn expired for <@{user_id}>.")
        except Exception:
            pass

# -------------------- NOTES management --------------------
@bot.group(name="notes", invoke_without_command=True)
async def notes_group(ctx):
    await ctx.send("Usage: `!notes add @user text` | `!notes remove @user` | `!notes look @user`")

@notes_group.command(name="add")
async def notes_add(ctx: commands.Context, member: discord.Member, *, text: str):
    if not await is_authorized_and_consume_override(ctx):
        return await ctx.send("⛔ No permission.")
    await database.add_note(ctx.guild.id, member.id, text, str(ctx.author))
    await ctx.send(f"📝 Note added for {member.mention}.")

@notes_group.command(name="remove")
async def notes_remove(ctx: commands.Context, member: discord.Member):
    if not await is_authorized_and_consume_override(ctx):
        return await ctx.send("⛔ No permission.")
    await database.remove_notes(ctx.guild.id, member.id)
    await ctx.send(f"🗑️ Notes removed for {member.mention}.")

@notes_group.command(name="look")
async def notes_look(ctx: commands.Context, member: discord.Member):
    if not await is_authorized_and_consume_override(ctx):
        return await ctx.send("⛔ No permission.")
    rows = await database.get_notes(ctx.guild.id, member.id)
    if not rows:
        return await ctx.send(f"No notes for {member.mention}.")
    # build a readable message
    lines = []
    for r in rows:
        nid, text, by_user, created_at = r
        lines.append(f"- [{created_at}] by {by_user}: {text}")
    # send in chunks if needed
    out = f"Notes for {member.mention}:\n" + "\n".join(lines)
    if len(out) <= 1900:
        await ctx.send(out)
    else:
        for i in range(0, len(out), 1900):
            await ctx.send(out[i:i+1900])

# -------------------- Startup scheduling for pending temp actions --------------------
async def schedule_pending_temp_actions():
    rows = await database.get_all_temp_actions()
    now = datetime.utcnow()
    for r in rows:
        row_id, guild_id, user_id, action_type, reason, by_user, expires_at = r
        try:
            expires = datetime.fromisoformat(expires_at)
        except Exception:
            # invalid data: remove it
            await database.remove_temp_action_by_id(row_id)
            continue
        secs = (expires - now).total_seconds()
        if secs <= 0:
            # expired already; perform immediate cleanup action
            if action_type == "tempban":
                await schedule_unban(guild_id, user_id, 0)
            elif action_type == "tempmute":
                await schedule_unmute(guild_id, user_id, 0)
            elif action_type == "tempwarn":
                await schedule_tempwarn_expire(guild_id, user_id, 0)
            await database.remove_temp_action_by_id(row_id)
        else:
            # schedule for future
            if action_type == "tempban":
                bot.loop.create_task(schedule_unban(guild_id, user_id, secs))
            elif action_type == "tempmute":
                bot.loop.create_task(schedule_unmute(guild_id, user_id, secs))
            elif action_type == "tempwarn":
                bot.loop.create_task(schedule_tempwarn_expire(guild_id, user_id, secs))

# -------------------- Run --------------------
if __name__ == "__main__":
    bot.run(TOKEN)
    
