# commands/disable_commands.py
import asyncio
from discord.ext import commands

# 지울 슬래시 명령어 이름들 (정확한 name)
TARGETS = [
    "상태", "국가정보", "명령어목록",
    "이동", "지역정보", "건설", "모집",
    "유닛정보", "장착",
    "예약", "예약확인", "예약취소", "이동ui", "집무실",
]

class DisableCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 모든 확장이 명령어를 등록한 뒤 제거되도록 지연 실행
        self.bot.loop.create_task(self._purge_and_sync())

    async def _purge_and_sync(self):
        # 봇 준비까지 대기
        await self.bot.wait_until_ready()
        # 혹시 늦게 로드되는 확장 대비 소폭 지연
        await asyncio.sleep(1.0)

        removed = []
        # 1) 글로벌 커맨드 제거
        for name in TARGETS:
            try:
                # chat input(슬래시) 기본 타입으로 제거
                self.bot.tree.remove_command(name)
                removed.append(name)
            except Exception:
                pass

        # 2) 글로벌 동기화
        try:
            await self.bot.tree.sync()
        except Exception as e:
            print(f"[disable] global sync error: {e}")

        # 3) 길드 스코프 커맨드도 제거/동기화 (길드별 등록 대응)
        for g in list(self.bot.guilds):
            try:
                for name in TARGETS:
                    try:
                        self.bot.tree.remove_command(name, guild=g)
                    except Exception:
                        pass
                await self.bot.tree.sync(guild=g)
            except Exception as e:
                print(f"[disable] guild {g.id} sync error: {e}")

        print(f"[disable] removed: {', '.join(removed) if removed else 'none'}")

async def setup(bot: commands.Bot):
    await bot.add_cog(DisableCommands(bot))
