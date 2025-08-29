# commands/nations.py
# 슬래시 명령어 제거 버전
# - 드롭다운으로 국가 선택 → 임베드 표시
import discord
from discord.ext import commands

# ====== 데이터: RED/BLUE 2개만 사용 ======
NATION_DATA = {
    "RED": {
        "id": "RED",
        "name": "붉은혁명단",
        "type": "국가",
        "desc": (
            "🔥 **붉은혁명단**\n"
            "— *“사람은 태어날 때부터 평등하다.”*\n"
            "억압과 불평등을 무너뜨리기 위해 봉기한 혁명 세력.\n"
            "지도자 **에브란 길라**를 중심으로, 강철 같은 규율과 불타는 의지로 "
            "새로운 세상을 건설하려 한다."
        )
    },
    "BLUE": {
        "id": "BLUE",
        "name": "알티스 공화국",
        "type": "국가",
        "desc": (
            "🦅 **알티스 공화국**\n"
            "— *겉은 민주주의, 속은 군국주의.*\n"
            "2월 혁명 이후 세워졌으나, 실권은 군부에 있다.\n"
            "장군 **소태훈**의 철권 통치 아래, 강력한 군사력으로 세력을 확장한다."
        )
    },
}
CAPITALS = {"RED": "A1","BLUE":"E5"}
DEFAULT_EMOJIS = {"tiles":{"RED":"🟥","BLUE":"🟦"},"capitals":{"RED":"⭐","BLUE":"☆"},"neutral":"🟩"}

# ----- 외부에서 참조할 때 쓸 헬퍼들 (services 스타일 호환) -----
def get_all_nations(): return list(NATION_DATA.values())
def get_nation_by_id(nid: str): return NATION_DATA.get(nid)
def get_nation_names(): return [v["name"] for v in NATION_DATA.values()]
def get_capital_of(nid: str) -> str: return CAPITALS.get(nid)
def get_default_emojis(): return DEFAULT_EMOJIS.copy()

def get_world_lore() -> str:
    return (
        "🌌 **세계관**\n"
        "2030년, 민간 연구기관의 타임머신 오작동으로 각기 다른 시대의 사람들이 "
        "이곳 **글라스세계**로 강제 이동했다. 통일과 분열을 반복하던 세계는 "
        "이제 **대격변**이라 불리는 주기적 혼란 속에서 다시 패권을 다툰다."
    )

# ----- UI -----
class NationSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=info["name"], value=info["id"]) for info in NATION_DATA.values()]
        super().__init__(placeholder="국가를 선택하세요", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        from cogs.ui_menu import build_tab_embed
        nid = self.values[0]
        n = NATION_DATA.get(nid)
        if not n:
            await interaction.response.send_message("❌ 국가 정보를 찾을 수 없습니다.", ephemeral=True); return
        tiles = DEFAULT_EMOJIS["tiles"]; caps = DEFAULT_EMOJIS["capitals"]
        desc = (
            n["desc"] + "\n\n"
            f"• 국가 ID: {nid}\n"
            f"• 수도 좌표: {CAPITALS.get(nid, '미지정')}\n"
            f"• 타일/수도: {tiles.get(nid,'⬜')} / {caps.get(nid,'⭐')}\n"
        )
        emb = build_tab_embed("info", f"🏳️ {n['name']}", desc)
        await interaction.response.send_message(embed=emb, ephemeral=True)

class NationsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(NationSelect())
        from cogs.ui_menu import BackHome
        self.add_item(BackHome())

class Nations(commands.Cog):
    def __init__(self, bot): self.bot = bot

async def setup(bot: commands.Bot):
    # 기존 Cog가 있으면 교체
    existing = bot.get_cog("Nations")
    if existing:
        await bot.remove_cog("Nations")
    await bot.add_cog(Nations(bot))
