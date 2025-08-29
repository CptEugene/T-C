# commands/ui_personnel.py
# 스탯: 통솔/무력/지력/매력, 각 최대 10
import os, json, traceback
import discord
from discord.ext import commands

DATA_DIR = "data"
MAX_STAT = 10

def _user(uid): return os.path.join(DATA_DIR, f"{uid}.json")
def _load(p, d=None):
    if not os.path.exists(p): return d
    try:
        with open(p,"r",encoding="utf-8") as f: return json.load(f)
    except: return d
def _save(p, data):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p,"w",encoding="utf-8") as f: json.dump(data,f,indent=4,ensure_ascii=False)

def _stats_line(st: dict) -> str:
    st = st or {}
    return f"통솔 {st.get('통솔',0)} / 무력 {st.get('무력',0)} / 지력 {st.get('지력',0)} / 매력 {st.get('매력',0)}"

def personnel_embed(user_id: int) -> discord.Embed:
    from .ui_menu import build_embed
    u = _load(_user(user_id), {}) or {}
    lvl = int(u.get("level",1)); xp = int(u.get("exp",0)); sp = int(u.get("stat_points",0))
    desc = (
        f"🧬 레벨 **{lvl}** (XP {xp}) / 남은 스탯포인트 **{sp}**\n"
        f"현재 스탯: {_stats_line(u.get('stats',{}))}\n\n"
        f"아래 버튼으로 **스탯 분배(지정)**를 진행하세요. (각 스탯 최대 {MAX_STAT})"
    )
    return build_embed("hr", "인사(조회/지정)", desc)

class StatAssignModal(discord.ui.Modal, title="스탯 분배(지정)"):
    tongs = discord.ui.TextInput(label="통솔 증가(숫자)", placeholder="예: 1", default="0", required=True, max_length=2)
    muryok = discord.ui.TextInput(label="무력 증가(숫자)", placeholder="예: 0", default="0", required=True, max_length=2)
    jiryok = discord.ui.TextInput(label="지력 증가(숫자)", placeholder="예: 0", default="0", required=True, max_length=2)
    maeryok = discord.ui.TextInput(label="매력 증가(숫자)", placeholder="예: 0", default="0", required=True, max_length=2)
    async def on_submit(self, interaction: discord.Interaction):
        try:
            f = _user(interaction.user.id)
            u = _load(f, {}) or {}
            sp = int(u.get("stat_points", 0))
            incs = {
                "통솔": int(str(self.tongs.value).strip() or 0),
                "무력": int(str(self.muryok.value).strip() or 0),
                "지력": int(str(self.jiryok.value).strip() or 0),
                "매력": int(str(self.maeryok.value).strip() or 0),
            }
            total = sum(max(0, v) for v in incs.values())
            if total <= 0:
                await interaction.response.send_message("⚠️ 증가값이 없습니다.", ephemeral=True); return
            if total > sp:
                await interaction.response.send_message(f"⚠️ 남은 포인트 {sp} 이내로 분배하세요.", ephemeral=True); return
            stats = u.setdefault("stats", {})
            for k, v in incs.items():
                if v < 0: v = 0
                stats[k] = min(MAX_STAT, int(stats.get(k,0)) + v)
            u["stat_points"] = sp - total
            _save(f, u)
            await interaction.response.send_message("✅ 스탯이 반영되었습니다.", ephemeral=True)
        except Exception:
            traceback.print_exc()
            try: await interaction.response.send_message("⚠️ 스탯 지정 중 오류", ephemeral=True)
            except: pass

class PersonnelView(discord.ui.View):
    def __init__(self): super().__init__(timeout=300)
    @discord.ui.button(label="스탯 지정", style=discord.ButtonStyle.primary, emoji="🧩")
    async def assign(self, interaction: discord.Interaction, _):
        await interaction.response.send_modal(StatAssignModal())

class UIPersonnel(commands.Cog):
    def __init__(self, bot): self.bot = bot

async def setup(bot: commands.Bot):
    await bot.add_cog(UIPersonnel(bot))
