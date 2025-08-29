# commands/ui_info.py
# 집무실(정보) 탭. 지역정보와 건설 UI를 분리(건설은 ‘건설’ 항목에서만).

import os, json
import discord
from discord.ext import commands

DATA_DIR = "data"
REGION_FILE = os.path.join(DATA_DIR, "region_data.json")
BUILDINGS_FILE = os.path.join(DATA_DIR, "buildings.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "game_settings.json")

LETTERS = "ABCDE"
W, H = 5, 5

def _load(p, d=None):
    if not os.path.exists(p): return d
    with open(p, "r", encoding="utf-8") as f: return json.load(f)

def _save(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f: json.dump(data, f, indent=4, ensure_ascii=False)

def _user(uid): return os.path.join(DATA_DIR, f"{uid}.json")

def _valid(c):
    if not c or len(c) < 2 or c[0].upper() not in LETTERS or not c[1:].isdigit():
        return False
    x = LETTERS.index(c[0].upper()); y = int(c[1:]) - 1
    return 0 <= x < W and 0 <= y < H

def _season():
    s = _load(SETTINGS_FILE, {}) or {}
    try: return int(s.get("season_number", 1))
    except: return 1

# economy helpers (optional)
try:
    from services.economy import ensure_account as _ensure_account, can_spend as _can_spend
except Exception:
    def _ensure_account(_): return None
    def _can_spend(_, __): return True

EMOJI_BY_RES = {"돈":"💰","식량":"🍚","연료":"⛽","징집병":"🪖"}

def _nation_file(nation_name: str) -> str:
    return os.path.join(DATA_DIR, f"{nation_name}.json")

def _load_nation_res(nation_name: str):
    try: _ensure_account(nation_name)
    except Exception: pass
    path = _nation_file(nation_name)
    if not os.path.exists(path): return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        res = data.get("자원", {}) or {}
        caps = data.get("자원한도", {}) or {}
        def _num(v):
            try:
                if isinstance(v, (int,float)): return int(v)
                if isinstance(v, str) and v.lstrip("-").isdigit(): return int(v)
            except: pass
            return None
        res = {k:_num(v) for k,v in res.items() if _num(v) is not None}
        caps= {k:_num(v) for k,v in caps.items() if _num(v) is not None}
        return res, caps
    except Exception:
        return None, None

def _fmt_num(n: int) -> str:
    try: return f"{int(n):,}"
    except: return str(n)

def _format_res_line(res: dict, caps: dict) -> str:
    if not isinstance(res, dict) or not res: return "(자원 데이터 없음)"
    order = ["돈","식량","연료","징집병"]
    extras = [k for k in res.keys() if k not in order]; extras.sort()
    keys = [k for k in order if k in res] + extras
    parts=[]
    for k in keys:
        v=res.get(k,0); cap=caps.get(k)
        parts.append(f"{EMOJI_BY_RES.get(k,'•')} {k} {_fmt_num(v)}/{_fmt_num(cap) if cap is not None else '∞'}")
    line="  |  ".join(parts)
    return line if len(line)<=400 else "\n".join([ "  |  ".join(parts[i:i+4]) for i in range(0,len(parts),4) ])

# --- 건설 목록/뷰 (재사용) ---
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
    return "비용 없음" if not cost else ", ".join(f"{k}:{_fmt_num(v)}" for k,v in cost.items())

class RegionBuildSelect(discord.ui.Select):
    def __init__(self, buildable_list, loc: str, nation_name: str):
        opts=[]
        for b in buildable_list[:25]:
            desc = f"{b['turns']}턴 | { _pretty_cost(b['cost'])[:70] }"
            opts.append(discord.SelectOption(label=b["name"], value=b["name"], description=desc))
        super().__init__(placeholder=f"{loc} 건설할 건물을 선택하세요", options=opts, min_values=1, max_values=1)
        self.buildable_list=buildable_list; self.loc=loc; self.nation_name=nation_name

    async def callback(self, interaction: discord.Interaction):
        fpath=_user(interaction.user.id)
        data=_load(fpath,{}) or {}
        if not data:
            await interaction.response.send_message("❌ 먼저 `/가입`을 해주세요!", ephemeral=True); return
        loc=data.get("location")
        if loc!=self.loc or not _valid(loc):
            await interaction.response.send_message("⚠️ 현재 위치 정보가 유효하지 않습니다.", ephemeral=True); return
        name=self.values[0]
        info=next((b for b in self.buildable_list if b["name"]==name), None)
        if not info:
            await interaction.response.send_message("⚠️ 건물 목록이 갱신되었습니다. 다시 시도하세요.", ephemeral=True); return

        # 사전 자원 체크(있으면)
        cost=info.get("cost",{}) or {}
        if cost and not _can_spend(self.nation_name, cost):
            await interaction.response.send_message(f"💸 `{name}` 건설 실패: 자원이 부족합니다. 필요: {_pretty_cost(cost)}", ephemeral=True); return

        reservations=data.get("reservations",[])
        if not isinstance(reservations, list): reservations=[]
        if len(reservations)>=4:
            await interaction.response.send_message("⚠️ 이미 4개의 행동이 예약되어 있어요.", ephemeral=True); return

        reservations.append({
            "action": f"건설 {name}",
            "type": "건설",
            "location": loc,
            "building": name,
            "turns_left": int(info.get("turns",1)),
            "cost_paid": False
        })
        data["reservations"]=reservations; _save(fpath,data)
        await interaction.response.send_message(f"🏗️ `{loc}`에 `{name}` 건설을 예약했습니다! ({len(reservations)}/4)", ephemeral=True)

class RegionBuildView(discord.ui.View):
    def __init__(self, buildable_list, loc: str, nation_name: str):
        super().__init__(timeout=300)
        if buildable_list:
            self.add_item(RegionBuildSelect(buildable_list, loc, nation_name))
        else:
            self.add_item(discord.ui.Select(placeholder="건설 가능한 건물이 없습니다.",
                                            options=[discord.SelectOption(label="건설 불가", value="none")],
                                            disabled=True))
        from .ui_menu import BackHome
        self.add_item(BackHome())

# ---- 정보 셀렉트: ‘건설’ 별도 항목 추가 ----
class InfoSelect(discord.ui.Select):
    def __init__(self):
        opts = [
            discord.SelectOption(label="내 상태", value="status"),
            discord.SelectOption(label="내 국가정보", value="nation"),
            discord.SelectOption(label="현재 지역정보", value="region"),
            discord.SelectOption(label="병종정보", value="unitdex"),
            discord.SelectOption(label="건설", value="build"),  # 🔹 신설
        ]
        super().__init__(placeholder="확인할 정보를 선택하세요", options=opts, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        from .ui_menu import build_tab_embed, build_home_embed

        user = _load(_user(interaction.user.id), {})
        if not user:
            embed = build_tab_embed("info", "❌ 먼저 /가입을 해주세요!")
            await interaction.response.edit_message(content=None, embed=embed, view=self.view); return

        choice = self.values[0]

        if choice == "status":
            embed_home = build_home_embed(interaction.user.id, interaction.user.display_name)
            embed = build_tab_embed("info", embed_home.title, embed_home.description)
            await interaction.response.edit_message(content=None, embed=embed, view=self.view); return

        if choice == "nation":
            try:
                from services.nations import NATION_DATA, CAPITALS, DEFAULT_EMOJIS
            except Exception:
                NATION_DATA, CAPITALS, DEFAULT_EMOJIS = {}, {}, {"tiles": {}, "capitals": {}}

            nation_name = user.get("nation")
            nation_obj = None
            # NATION_DATA 값 구조를 가정: {id: {"id":id, "name":..., "desc":...}}
            for n in NATION_DATA.values():
                if n.get("name") == nation_name:
                    nation_obj = n; break
            if not nation_obj:
                embed = build_tab_embed("info", "⚠️ 소속 국가 정보를 찾지 못했습니다.")
                await interaction.response.edit_message(content=None, embed=embed, view=self.view); return

            nid = nation_obj.get("id", "?")
            tiles = DEFAULT_EMOJIS.get("tiles", {})
            caps = DEFAULT_EMOJIS.get("capitals", {})
            res, cap = _load_nation_res(nation_obj["name"])
            res_line = _format_res_line(res or {}, cap or {})
            desc = (
                f"{nation_obj.get('desc','설명 없음')}\n\n"
                f"• 국가 ID: {nid}\n"
                f"• 수도 좌표: {CAPITALS.get(nid,'미지정')}\n"
                f"• 타일/수도: {tiles.get(nid,'⬜')} / {caps.get(nid,'⭐')}\n"
                f"• 국가 자원: {res_line}"
            )
            embed = build_tab_embed("info", f"🏳️ {nation_obj['name']}", desc)
            await interaction.response.edit_message(content=None, embed=embed, view=self.view); return

        if choice == "region":
            loc = user.get("location"); region = _load(REGION_FILE, {})
            slot = region.get(loc)
            if not (_valid(loc) and isinstance(slot, dict)):
                embed = build_tab_embed("info", "⚠️ 현재 지역 정보를 찾지 못했습니다.")
                await interaction.response.edit_message(content=None, embed=embed, view=self.view); return
            unit_list = slot.get("units") or ([] if not slot.get("unit") else [slot["unit"]])
            unit_display = ", ".join(f"<@{u}>" for u in unit_list) if unit_list else "없음"
            buildings = slot.get("buildings", [])
            building_display = ", ".join(buildings) if buildings else "(건물 없음)"
            neutral_units = slot.get("neutral_units", [])
            neutral_total = int(slot.get("neutral_total", 0) or 0)
            approx = max(0, (neutral_total // 10) * 10)

            # 🔸 건설 드롭다운은 여기 X. 실제 건설은 '건설' 항목에서만!
            desc = (
                f"📍 지역: {loc}\n"
                f"• 지역 타입: {slot.get('type','알 수 없음')}\n"
                f"• 주둔 유저: {unit_display}\n"
                f"• 건물: {building_display}\n"
                f"• 중립 병력: {'없음' if not neutral_units else f'부대 {len(neutral_units)}개, 대략 {approx}명'}\n"
                f"\n🧱 건설은 **집무실 → 건설** 탭에서 진행하세요."
            )
            embed = build_tab_embed("info", "📍 현재 지역정보", desc)
            await interaction.response.edit_message(content=None, embed=embed, view=self.view); return

        if choice == "build":
            loc = user.get("location"); region = _load(REGION_FILE, {})
            slot = region.get(loc)
            if not (_valid(loc) and isinstance(slot, dict)):
                embed = build_tab_embed("info", "⚠️ 현재 지역 정보를 찾지 못했습니다.")
                await interaction.response.edit_message(content=None, embed=embed, view=self.view); return
            nation_name = user.get("nation") or "무소속"
            buildable = _read_buildables_for_region(loc, slot)
            embed = build_tab_embed("info", "🧱 건설", f"현재 위치 **{loc}**\n건설 가능: {', '.join(b['name'] for b in buildable) if buildable else '없음'}")
            view = RegionBuildView(buildable, loc, nation_name)
            await interaction.response.edit_message(content=None, embed=embed, view=view); return
        
        if choice == "unitdex":  # 🆕 병종 도감 열기
            from .ui_units import UnitDexView  # 지연 import
            view = UnitDexView()               # 현재 시즌 자동 인식
            emb = view.build_embed()
            await interaction.response.edit_message(content=None, embed=emb, view=view); return

class InfoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(InfoSelect())
        from .ui_menu import BackHome
        self.add_item(BackHome())

class UIInfoCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

async def setup(bot: commands.Bot):
    await bot.add_cog(UIInfoCog(bot))
