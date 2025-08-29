# commands/settings.py
# 슬래시 명령어 제거 버전
# - 관리자 전용: 시즌 재시작, 지도 재생성, 알림 채널 지정 UI
import os, json, shutil, random, traceback
import discord
from discord.ext import commands
from datetime import datetime

SETTINGS_FILE = "data/game_settings.json"
BACKUP_DIR = "backup"
MAP_PATH = "data/map.json"
REGION_PATH = "data/region_data.json"
AREA_TYPE_FILE = "data/area_types.json"
NATIONS_FILE = "data/nations.json"

LETTERS = "ABCDE"
MAP_WIDTH = 5
MAP_HEIGHT = 5

# 시즌 번호 맵 (외부 config 없이 내부 정의)
SEASON_MAP = {1: "중세", 2: "근대", 3: "해상"}

def _load(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path,"r",encoding="utf-8") as f: return json.load(f)
    except Exception:
        return default

def _save(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path,"w",encoding="utf-8") as f: json.dump(data,f,indent=2,ensure_ascii=False)

def _season() -> int:
    s = _load(SETTINGS_FILE, {}) or {}
    try: return int(s.get("season_number", 1))
    except: return 1

def _settings() -> dict:
    return _load(SETTINGS_FILE, {}) or {}

def _load_area_types_for_season(season: int) -> dict:
    at = _load(AREA_TYPE_FILE, {}) or {}
    usable = {}
    for name, info in (at.items() if isinstance(at, dict) else []):
        seasons = (info or {}).get("사용시즌", [])
        if isinstance(seasons, list) and season in seasons:
            usable[name] = info or {}
    return usable

def _load_unit_defs(season: int) -> dict:
    path = os.path.join("data", f"units_season{season}.json")
    return _load(path, {}) or {}

def _is_ship(unit_info: dict) -> bool:
    return str((unit_info or {}).get("표기방식", "")).strip() == "함선"

def _pick_area_type(usable_types: dict, season: int, is_capital: bool) -> str:
    types = list(usable_types.keys())
    if not types:
        return "마을" if season in (1,2) else "섬"
    if is_capital:
        return ("도시" if season in (1,2) else "섬") if (("도시" if season in (1,2) else "섬") in types) else random.choice(types)
    if season in (1,2):
        weights = [{"마을":3,"도시":2,"성":1}.get(t,1) for t in types]
        return random.choices(types, weights=weights, k=1)[0]
    else:
        weights = [{"바다":5,"섬":2}.get(t,1) for t in types]
        return random.choices(types, weights=weights, k=1)[0]

def _build_neutral_units_full(area_type: str, unit_defs: dict, season: int):
    if not unit_defs: return [], 0
    items = list(unit_defs.items())
    if not items: return [], 0
    land_pool = [name for name, info in items if not _is_ship(info)]
    ship_pool = [name for name, info in items if _is_ship(info)]
    if area_type == "바다":
        pool = ship_pool if ship_pool else land_pool
        cnt_min, cnt_max = 1, 3
    elif area_type == "섬":
        base = land_pool if land_pool else [n for n,_ in items]
        if ship_pool and random.random() < 0.3:
            base = base + random.sample(ship_pool, k=min(1, len(ship_pool)))
        pool = base; cnt_min, cnt_max = 0, 2
    else:
        pool = land_pool if land_pool else [n for n,_ in items]
        if area_type == "마을":
            cnt_min, cnt_max = 0, 2
        elif area_type == "도시":
            cnt_min, cnt_max = 1, 3
        else:
            cnt_min, cnt_max = 2, 4
    if not pool: return [], 0
    n_units = random.randint(cnt_min, cnt_max)
    units=[]; total=0
    for _ in range(n_units):
        name = random.choice(pool)
        info = unit_defs.get(name,{})
        maxp = int(info.get("인구", 500) or 500)
        pop = maxp
        units.append({"이름": name, "인구": pop, "최대인구": maxp})
        total += pop
    return units, total

def _generate_world_for_season(season: int):
    st = _settings()
    neutral_tile = st.get("neutral_tile", "🟩")
    capital_symbols = st.get("nation_capitals", {"RED":"⭐", "BLUE":"☆"})
    grid = [[neutral_tile for _ in range(MAP_WIDTH)] for _ in range(MAP_HEIGHT)]
    region = {}
    usable_types = _load_area_types_for_season(season)
    unit_defs = _load_unit_defs(season)

    try:
        from .nations import CAPITALS
    except Exception:
        CAPITALS = {}

    for y in range(MAP_HEIGHT):
        for x in range(MAP_WIDTH):
            code = f"{LETTERS[x]}{y+1}"
            is_cap = any((isinstance(v, str) and v == code) for v in (CAPITALS or {}).values())

            area_type = _pick_area_type(usable_types, season, is_cap)
            owner = "중립"; capital = False
            if is_cap:
                for nid, coord in (CAPITALS or {}).items():
                    if coord == code:
                        owner = nid; capital = True; break
                sym = capital_symbols.get(owner, "⭐")
                grid[y][x] = sym

            slot = {
                "type": area_type, "owner": owner, "capital": capital,
                "unit": None, "units": [], "unit_size": 0,
                "buildings": [], "resource_bonus": None, "enemy_presence": False
            }

            if owner == "중립" and not capital:
                nu, total = _build_neutral_units_full(area_type, unit_defs, season)
                if nu:
                    slot["neutral_units"] = nu
                    slot["neutral_total"] = total
                    slot["unit_size"] = total
                    slot["enemy_presence"] = True
            region[code] = slot
    return grid, region

def _render_map(grid):
    header = "     " + "  ".join(f" {chr(65+i)} " for i in range(MAP_WIDTH))
    lines = [f"-지도-\n{header}"]
    for y, row in enumerate(grid):
        row_str = "  ".join(f"[{cell}]" for cell in row)
        lines.append(f"{str(y+1).rjust(2)}  {row_str}")
    return "\n".join(lines)

# ---------------- UI ----------------
class SeasonRestartModal(discord.ui.Modal, title="게임 재시작"):
    season_no = discord.ui.TextInput(label="시즌 번호 (1=중세, 2=근대, 3=해상)", placeholder="예: 1", required=True, max_length=2)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⚠️ 관리자만 사용 가능", ephemeral=True); return
        try:
            sn = int(str(self.season_no.value).strip())
            if sn not in SEASON_MAP:
                await interaction.response.send_message("❌ 유효하지 않은 시즌 번호입니다. (1/2/3)", ephemeral=True); return

            # 백업: 유저파일(숫자.json) + nations.json
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(BACKUP_DIR, f"backup_{timestamp}")
            os.makedirs(backup_path, exist_ok=True)
            deleted = 0
            if os.path.exists("data"):
                for filename in os.listdir("data"):
                    if not filename.endswith(".json"): continue
                    base = filename[:-5]
                    is_user_file = base.isdigit()
                    is_nations = (filename == os.path.basename(NATIONS_FILE))
                    if is_user_file or is_nations:
                        src = os.path.join("data", filename)
                        dst = os.path.join(backup_path, filename)
                        try: shutil.copy2(src, dst)
                        except: pass
                        try: os.remove(src); deleted += 1
                        except: pass

            settings = _load(SETTINGS_FILE, {})
            settings["season_number"] = sn
            settings.setdefault("nation_tiles", {"RED":"🟥","BLUE":"🟦"})
            settings.setdefault("nation_capitals", {"RED":"⭐","BLUE":"☆"})
            settings.setdefault("neutral_tile", "🟩")
            _save(SETTINGS_FILE, settings)

            grid, region = _generate_world_for_season(sn)
            _save(MAP_PATH, grid); _save(REGION_PATH, region)

            rendered = _render_map(grid)
            await interaction.response.send_message(
                f"♻️ 게임 재시작 완료 (시즌 {sn}:{SEASON_MAP[sn]})\n"
                f"📁 백업: {backup_path}\n"
                f"🗑️ 삭제된 파일: **{deleted}개**\n"
                f"🗺️ 새 월드:\n```\n{rendered}\n```",
                ephemeral=True
            )
        except Exception:
            traceback.print_exc()
            try: await interaction.response.send_message("⚠️ 재시작 중 오류가 발생했습니다.", ephemeral=True)
            except: pass

class SettingsView(discord.ui.View):
    def __init__(self): super().__init__(timeout=300)

    @discord.ui.button(label="게임 재시작", style=discord.ButtonStyle.danger, emoji="♻️")
    async def restart(self, interaction: discord.Interaction, _):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⚠️ 관리자만 사용 가능", ephemeral=True); return
        await interaction.response.send_modal(SeasonRestartModal())

    @discord.ui.button(label="지도 재생성", style=discord.ButtonStyle.primary, emoji="🗺️")
    async def regen_map(self, interaction: discord.Interaction, _):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⚠️ 관리자만 사용 가능", ephemeral=True); return
        try:
            season = _season()
            grid, region = _generate_world_for_season(season)
            _save(MAP_PATH, grid); _save(REGION_PATH, region)
            rendered = _render_map(grid)
            await interaction.response.send_message(
                f"🗺️ 지도를 새로 생성했습니다! (시즌 {season})\n```\n{rendered}\n```",
                ephemeral=True
            )
        except Exception:
            traceback.print_exc()
            try: await interaction.response.send_message("⚠️ 지도 생성 중 오류", ephemeral=True)
            except: pass

async def 알림채널(self, interaction: discord.Interaction, 채널: discord.TextChannel=None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("⚠️ 관리자만 사용할 수 있어요!", ephemeral=True); return
    settings = self._settings()
    if 채널 is None:
        settings.pop("announce_channel", None)
        _save(SETTINGS_FILE, settings)
        await interaction.response.send_message("🔕 알림 채널 설정을 해제했습니다.", ephemeral=True); return
    settings["announce_channel"] = int(채널.id)
    _save(SETTINGS_FILE, settings)
    await interaction.response.send_message(f"🔔 알림 채널을 {채널.mention} 로 설정했습니다.", ephemeral=True)

class GameSettings(commands.Cog):
    def __init__(self, bot): self.bot = bot

async def setup(bot: commands.Bot):
    await bot.add_cog(GameSettings(bot))
