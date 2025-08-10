# database.py
import aiosqlite
import asyncio
from datetime import datetime

DB_FILE = "moderation.db"

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY,
            guild_id INTEGER,
            user_id INTEGER,
            text TEXT,
            by_user TEXT,
            created_at TEXT
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY,
            guild_id INTEGER,
            user_id INTEGER,
            reason TEXT,
            by_user TEXT,
            created_at TEXT,
            expires_at TEXT
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS temp_actions (
            id INTEGER PRIMARY KEY,
            guild_id INTEGER,
            user_id INTEGER,
            action_type TEXT, -- 'tempban','tempmute','tempwarn'
            reason TEXT,
            by_user TEXT,
            expires_at TEXT
        )""")
        await db.commit()

# Notes
async def add_note(guild_id:int, user_id:int, text:str, by_user:str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO notes (guild_id,user_id,text,by_user,created_at) VALUES (?, ?, ?, ?, ?)",
                         (guild_id, user_id, text, by_user, datetime.utcnow().isoformat()))
        await db.commit()

async def get_notes(guild_id:int, user_id:int):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT id,text,by_user,created_at FROM notes WHERE guild_id=? AND user_id=? ORDER BY id DESC",
                               (guild_id, user_id))
        rows = await cur.fetchall()
        return rows

async def remove_notes(guild_id:int, user_id:int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM notes WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        await db.commit()

# Warnings
async def add_warning(guild_id:int, user_id:int, reason:str, by_user:str, expires_at=None):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO warnings (guild_id,user_id,reason,by_user,created_at,expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (guild_id, user_id, reason, by_user, datetime.utcnow().isoformat(), expires_at))
        await db.commit()

async def get_warnings(guild_id:int, user_id:int):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT id,reason,by_user,created_at,expires_at FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC",
                               (guild_id, user_id))
        return await cur.fetchall()

async def remove_warning_by_id(warn_id:int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM warnings WHERE id=?", (warn_id,))
        await db.commit()

async def remove_warnings_all(guild_id:int, user_id:int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM warnings WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        await db.commit()

# Temp actions
async def add_temp_action(guild_id:int, user_id:int, action_type:str, reason:str, by_user:str, expires_at:str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO temp_actions (guild_id,user_id,action_type,reason,by_user,expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (guild_id, user_id, action_type, reason, by_user, expires_at))
        await db.commit()

async def remove_temp_action_by_id(row_id:int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM temp_actions WHERE id=?", (row_id,))
        await db.commit()

async def get_all_temp_actions():
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT id,guild_id,user_id,action_type,reason,by_user,expires_at FROM temp_actions")
        return await cur.fetchall()

async def get_temp_actions_for_user(guild_id:int, user_id:int):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT id,action_type,expires_at FROM temp_actions WHERE guild_id=? AND user_id=?",
                               (guild_id, user_id))
        return await cur.fetchall()
