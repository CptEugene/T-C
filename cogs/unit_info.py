# commands/unit_info.py
import discord
from discord import app_commands
from discord.ext import commands
import os, json

UNIT_FILES = {
    1: "data/units_season1.json",
    2: "data/units_season2.json",
    3: "data/units_season3.json"
}

class UnitInfo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def find_unit(self, name):
        for season, filepath in UNIT_FILES.items():
            if not os.path.exists(filepath):
                continue
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            for unit in data:
                if unit["이름"] == name:
                    return unit, season
        return None, None

    @app_commands.command(name="유닛정보", description="특정 유닛의 정보를 확인합니다.")
    @app_commands.describe(유닛이름="예: 징집창병대, 장창병, 화승총병 등")
    async def 유닛정보(self, interaction: discord.Interaction, 유닛이름: str):
        unit, season = self.find_unit(유닛이름)

        if not unit:
            await interaction.response.send_message("❌ 해당 유닛을 찾을 수 없습니다.", ephemeral=True)
            return

        embed = discord.Embed(title=f"🪖 유닛 정보 - {unit['이름']}", color=0x8E44AD)
        embed.add_field(name="티어", value=str(unit.get("티어", "?")), inline=True)
        embed.add_field(name="병종", value=unit.get("병종", "-"), inline=True)
        embed.add_field(name="유형", value=unit.get("유형", "-"), inline=True)

        embed.add_field(name="일반공격", value=str(unit.get("일반공격", 0)), inline=True)
        embed.add_field(name="일반방어", value=str(unit.get("일반방어", 0)), inline=True)
        embed.add_field(name="특수공격", value=str(unit.get("특수공격", 0)), inline=True)
        embed.add_field(name="특수방어", value=str(unit.get("특수방어", 0)), inline=True)

        embed.add_field(name="요구능력", value=unit.get("요구능력", "-"), inline=True)
        embed.add_field(name="요구능력치", value=str(unit.get("요구능력치", 0)), inline=True)
        embed.add_field(name="시즌", value=f"시즌 {season}", inline=True)

        if unit.get("특수능력"):
            embed.add_field(name="특수능력", value=unit["특수능력"], inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(UnitInfo(bot))
