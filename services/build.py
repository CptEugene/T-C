# commands/build.py
# 슬래시 명령어 제거 버전
# - "건설" 버튼 → 현재 지역의 건설 가능 목록 드롭다운
# - 선택 시 예약(push) 저장
import os, json, traceback
import discord
from discord.ext import commands

DATA_DIR = "data"
REGION_FILE = os.path.join(DATA_DIR, "region_data.json")
BUILDINGS_FILE = os.path.join(DATA_DIR, "buildings.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "game_settings.json")

MAX_RES = 4

def _load(p, d=None):
    if not os.path.exists(p): return d
    try:
        with open(p, "r", encoding="utf-8") as f: return json.load(f)
    except Exception:
        return d

def _save(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f: json.dump(data, f, indent=4, ensure_ascii=False)

def _user(uid): return os.path.join(DATA_DIR, f"{uid}.json")

def _season():
    s = _load(SETTINGS_FILE, {}) or {}
    try: return int(s.get("season_number", 1))
    except: return 1

def _read_buildables_for_region(loc: str, region_slot: dict):
    bdefs = _load(BUILDINGS_FILE, {}) or {}
    season = _season()
    existing = set((region_slot or {}).get("buildings", []) or [])
    out=[]
    for name, info in bdefs.items():
        seasons = (info or {}).get("요구시즌")
        if isinstance(seasons, list) and seasons and season not in seasons: continue
        if name in existing: continue
        turns = int((info or {}).get("건설시간", 1))
        cost  = {k:int(v) for k,v in ((info or {}).get("건설비용") or {}).items()} if isinstance((info or {}).get("건설비용"),dict) else {}
        out.append({"name":name,"turns":turns,"cost":cost})
    return out

def _pretty_cost(cost: dict) -> str:
    return "비용 없음" if not cost else ", ".join(f"{k}:{v:,}" for k,v in cost.items())

class BuildSelect(discord.ui.Select):
    def __init__(self, buildables, loc):
        opts=[]
        for b in buildables[:25]:
            desc = f"{b['turns']}턴 | {_pretty_cost(b['cost'])[:70]}"
            opts.append(discord.SelectOption(label=b["name"], value=b["name"], description=desc))
        super().__init__(placeholder=f"{loc} 건설할 건물을 선택", options=opts, min_values=1, max_values=1)
        self.buildables=buildables; self.loc=loc

    async def callback(self, interaction: discord.Interaction):
        try:
            try: await interaction.response.defer(ephemeral=True, thinking=False)
            except: pass
            name = self.values[0]
            info = next((b for b in self.buildables if b["name"]==name), None)
            if not info:
                await interaction.followup.send("⚠️ 목록이 갱신되었습니다. 다시 시도하세요.", ephemeral=True); return
            fpath=_user(interaction.user.id)
            data=_load(fpath,{}) or {}
            if not data:
                await interaction.followup.send("❌ 먼저 `/가입`을 해주세요!", ephemeral=True); return
            if data.get("location") != self.loc:
                await interaction.followup.send("⚠️ 현재 위치가 변경되었습니다. 다시 열어주세요.", ephemeral=True); return
            rsv=data.get("reservations",[])
            if len(rsv)>=MAX_RES:
                await interaction.followup.send(f"⚠️ 이미 {MAX_RES}개의 행동이 예약되어 있어요.", ephemeral=True); return
            rsv.append({
                "action": f"건설 {name}",
                "type": "건설",
                "location": self.loc,
                "building": name,
                "turns_left": int(info.get("turns",1)),
                "cost_paid": False
            })
            data["reservations"]=rsv; _save(fpath,data)
            await interaction.followup.send(f"🏗️ `{self.loc}`에 `{name}` 건설을 예약했습니다! ({len(rsv)}/{MAX_RES})", ephemeral=True)
        except Exception:
            traceback.print_exc()
            try: await interaction.followup.send("⚠️ 처리 중 오류가 발생했습니다.", ephemeral=True)
            except: pass

class BuildView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        fpath=_user(user_id)
        data=_load(fpath,{}) or {}
        from cogs.ui_menu import build_tab_embed
        if not data:
            self.embed = build_tab_embed("info", "🧱 건설", "❌ 먼저 /가입을 해주세요!")
            return
        loc = data.get("location")
        region = _load(REGION_FILE, {})
        slot = region.get(loc) if isinstance(region, dict) else None
        if not (loc and isinstance(slot, dict)):
            self.embed = build_tab_embed("info", "🧱 건설", "⚠️ 현재 위치 정보를 찾지 못했습니다.")
            return
        buildables = _read_buildables_for_region(loc, slot)
        names = ", ".join(b["name"] for b in buildables) if buildables else "없음"
        self.embed = build_tab_embed("info", "🧱 건설", f"현재 위치 **{loc}**\n건설 가능: {names}")
        if buildables:
            self.add_item(BuildSelect(buildables, loc))
        else:
            sel = discord.ui.Select(placeholder="건설 가능 없음", options=[discord.SelectOption(label="없음", value="none")], disabled=True)
            self.add_item(sel)
        from cogs.ui_menu import BackHome
        self.add_item(BackHome())

class BuildCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

async def setup(bot: commands.Bot):
    await bot.add_cog(BuildCog(bot))
