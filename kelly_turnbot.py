# kelly_turnbot.py  (UI-Only)
import os
import discord
from discord.ext import commands
import asyncio

from config import BOT_TOKEN  # 토큰은 별도 관리
from services.scheduler import start_background_tasks

# --- Intents ---
intents = discord.Intents.default()
intents.guilds = True
intents.message_content = False  # UI 전용이면 굳이 필요 없음 (명령 텍스트 파싱 X)

# --- Bot ---
bot = commands.Bot(
    command_prefix="!",  # 슬래시 위주이지만 Bot 객체는 필요
    intents=intents,
)

# --- on_ready ---
@bot.event
async def on_ready():
    # 앱 커맨드(UI) 동기화 (필요 시)
    try:
        synced = await bot.tree.sync()
        print(f"✅ app_commands sync: {len(synced)}개")
    except Exception as e:
        print(f"⚠️ app_commands sync 실패: {e}")

    # 스케줄러(턴 루프) 시작
    try:
        start_background_tasks(bot)
        print("✅ scheduler started")
    except Exception as e:
        print(f"❌ scheduler start 실패: {e}")

# --- main ---
async def main():
    async with bot:
        # ✅ UI 전용 Cogs만 로드 (cogs/ 경로)
        modules = [
            "cogs.ui_actions",
            "cogs.ui_info",
            "cogs.ui_map",
            "cogs.ui_army",
            "cogs.ui_menu",
        ]

        for m in modules:
            try:
                await bot.load_extension(m)
                print(f"✅ {m} 로드")
            except Exception as e:
                print(f"❌ {m} 로드 실패: {e}")

        # 구형 슬래시/명령 코그는 더 이상 로드하지 않음
        # - commands.register, commands.reserve, commands.nations, commands.settings, commands.disable_commands

        try:
            await bot.start(BOT_TOKEN)
        except Exception as e:
            print(f"❌ 봇 실행 실패: {e}")

if __name__ == "__main__":
    asyncio.run(main())
