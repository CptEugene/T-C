# commands/ui_internal.py
import os, json, traceback
import discord
from discord.ext import commands

DATA_DIR = "data"
def _user(uid): return os.path.join(DATA_DIR, f"{uid}.json")

class SpecialSelect(discord.ui.Select):
    def __init__(self):
        ops = [
            discord.SelectOption(label="징수(세금)", value="징수", description="국가 돈 +200"),
            discord.SelectOption(label="치안강화", value="치안강화", description="유저 사기 +5"),
        ]
        super().__init__(placeholder="특수 행동 선택", options=ops, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        try:
            f = _user(interaction.user.id)
            if not os.path.exists(f):
                await interaction.response.send_message("❌ 먼저 `/가입`을 해주세요!", ephemeral=True); return
            with open(f,"r",encoding="utf-8") as rf: data=json.load(rf)
            rsv = data.get("reservations", [])
            if len(rsv) >= 4:
                await interaction.response.send_message("⚠️ 이미 4개 예약이 있습니다.", ephemeral=True); return
            action = self.values[0]
            rsv.append({"type":"특수","action":f"특수 {action}","special":action})
            data["reservations"]=rsv
            with open(f,"w",encoding="utf-8") as wf: json.dump(data,wf,indent=4,ensure_ascii=False)
            await interaction.response.send_message(f"✨ 특수 `{action}` 예약 완료!", ephemeral=True)
        except Exception:
            traceback.print_exc()
            await interaction.response.send_message("⚠️ 특수 예약 중 오류", ephemeral=True)

class InternalView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(BuildBtn())
        self.add_item(SpecialBtn())
        from .ui_menu import BackHome
        self.add_item(BackHome())

class BuildBtn(discord.ui.Button):
    def __init__(self): super().__init__(label="건설", style=discord.ButtonStyle.primary, emoji="🏗️")
    async def callback(self, interaction: discord.Interaction):
        from services.build import BuildView
        from .ui_menu import build_embed
        view = BuildView(interaction.user.id)
        emb = build_embed("int","건설","현재 지역의 **건설 가능 목록**입니다.")
        await interaction.response.edit_message(embed=emb, view=view)

class SpecialBtn(discord.ui.Button):
    def __init__(self): super().__init__(label="특수", style=discord.ButtonStyle.secondary, emoji="✨")
    async def callback(self, interaction: discord.Interaction):
        from .ui_menu import build_embed, BackHome
        v = discord.ui.View(timeout=180)
        v.add_item(SpecialSelect()); v.add_item(BackHome())
        await interaction.response.edit_message(embed=build_embed("int","특수","원하는 **특수 행동**을 선택해 예약하세요."), view=v)

class UIInternal(commands.Cog):
    def __init__(self, bot): self.bot = bot

async def setup(bot: commands.Bot):
    await bot.add_cog(UIInternal(bot))
