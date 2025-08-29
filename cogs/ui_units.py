# commands/unit_info.py
# 슬래시 명령어 제거 버전
# - "유닛 검색" 버튼 → 모달로 이름 입력 → 시즌/정의 파일에서 검색 후 임베드 표시
# - 어디서든 View를 불러 쓸 수 있도록 공개 View 제공 (예: 집무실 탭에 붙이기)

import os, json, traceback
import discord
from discord.ext import commands

DATA_DIR = "data"
UNIT_FILES = {
    1: os.path.join(DATA_DIR, "units_season1.json"),
    2: os.path.join(DATA_DIR, "units_season2.json"),
    3: os.path.join(DATA_DIR, "units_season3.json"),
}
SETTINGS_FILE = os.path.join(DATA_DIR, "game_settings.json")

def _load(p, d=None):
    if not os.path.exists(p): return d
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return d

def _season():
    s = _load(SETTINGS_FILE, {}) or {}
    try: return int(s.get("season_number", 1))
    except: return 1

def _unit_defs():
    path = UNIT_FILES.get(_season())
    return _load(path, {}) or {}

def _find_unit(name: str):
    # 현재 시즌 우선 → 없으면 전 시즌 순회
    defs_now = _unit_defs()
    if name in defs_now:
        return defs_now[name], _season()
    for sn, fp in UNIT_FILES.items():
        data = _load(fp, {}) or {}
        if name in data:
            return data[name], sn
    return None, None

def _build_embed(unit_name: str, info: dict, season: int) -> discord.Embed:
    from .ui_menu import build_tab_embed
    def _as_int(v, d=0):
        try:
            if isinstance(v, (int,float)): return int(v)
            if isinstance(v, str) and v.strip().lstrip("-").isdigit(): return int(v)
        except: pass
        return d
    t = info.get("티어", "?")
    cls = info.get("병종유형") or info.get("병종") or "-"
    atk = _as_int(info.get("공격력", info.get("일반공격", 0)))
    dfn = _as_int(info.get("방어력", info.get("일반방어", 0)))
    sp_atk = _as_int(info.get("특수공격", 0))
    sp_def = _as_int(info.get("특수방어", 0))
    req_stat  = info.get("요구능력", "-")
    req_value = info.get("요구능력치", "-")
    spec = info.get("특수능력")
    pop  = _as_int(info.get("인구", 0))
    img  = info.get("이미지")

    lines = [
        f"• 병종유형: **{cls}**  | 티어 **{t}**",
        f"• 전투력: ⚔ {atk} / 🛡 {dfn} (특수: ⚡{sp_atk} / 🧿{sp_def})",
        f"• 인구(기본/최대 정의): **{pop}**",
        f"• 요구능력: **{req_stat} {req_value}**",
        f"• 시즌: **{season}**",
    ]
    if spec:
        if isinstance(spec, list):
            spec_txt = ", ".join(map(str, spec))
        else:
            spec_txt = str(spec)
        lines.append(f"• 특수능력: {spec_txt}")

    emb = build_tab_embed("info", f"🧾 {unit_name} (시즌 {season})", "\n".join(lines))
    if img:
        try: emb.set_image(url=img)
        except: pass
    return emb

class UnitSearchModal(discord.ui.Modal, title="유닛 이름 입력"):
    unit_name = discord.ui.TextInput(label="유닛 이름", placeholder="예: 장창병, 화승총병 …", required=True, max_length=50)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            name = str(self.unit_name.value).strip()
            info, season = _find_unit(name)
            if not info:
                await interaction.response.send_message("❌ 해당 유닛을 찾을 수 없습니다.", ephemeral=True); return
            emb = _build_embed(name, info, season)
            await interaction.response.send_message(embed=emb, ephemeral=True)
        except Exception:
            traceback.print_exc()
            try: await interaction.response.send_message("⚠️ 처리 중 오류가 발생했습니다.", ephemeral=True)
            except: pass

class UnitInfoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="유닛 검색", style=discord.ButtonStyle.primary, emoji="🔎")
    async def search_unit(self, interaction: discord.Interaction, _):
        await interaction.response.send_modal(UnitSearchModal())

class UnitInfo(commands.Cog):
    def __init__(self, bot): self.bot = bot

async def setup(bot: commands.Bot):
    await bot.add_cog(UnitInfo(bot))
