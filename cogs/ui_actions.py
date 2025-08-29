# commands/ui_actions.py
import discord
from discord import app_commands
from discord.ext import commands
import os, json, traceback
from typing import List, Dict

DATA_DIR = "data"
SETTINGS_FILE = os.path.join(DATA_DIR, "game_settings.json")
REGION_FILE = os.path.join(DATA_DIR, "region_data.json")

LETTERS = "ABCDE"
MAP_WIDTH = 5
MAP_HEIGHT = 5
MAX_RESERVATIONS = 4

# -------------------- 공용 유틸 --------------------
def load_json(path: str, default):
    if not os.path.exists(path): return default
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception:
        return default

def save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def user_path(uid: int) -> str:
    return os.path.join(DATA_DIR, f"{uid}.json")

def get_season_number() -> int:
    settings = load_json(SETTINGS_FILE, {}) or {}
    return int(settings.get("season_number", 1))

def get_unit_defs_path(season: int) -> str:
    return os.path.join(DATA_DIR, f"units_season{season}.json")

def is_valid_coord(coord: str) -> bool:
    if not isinstance(coord, str) or len(coord) < 2: return False
    x, y = coord[0].upper(), coord[1:]
    if x not in LETTERS or not y.isdigit(): return False
    xi, yi = LETTERS.index(x), int(y) - 1
    return 0 <= xi < MAP_WIDTH and 0 <= yi < MAP_HEIGHT

def load_map_grid():
    return load_json(REGION_FILE, {})

def is_passable(coord: str, _grid=None) -> bool:
    grid = _grid if isinstance(_grid, dict) else load_json(REGION_FILE, {})
    return isinstance(grid, dict) and coord in grid

def slot_limit(user: dict) -> int:
    통솔 = int(user.get("stats", {}).get("통솔", 0))
    return 4 + 통솔

# ----- 경제 모듈(있으면 사용) -----
try:
    from services.economy import (
        ensure_account as _ensure_account,
        can_spend as _can_spend,
        spend as _spend,
        load_nation as _load_nation,
    )
except Exception:
    def _ensure_account(_nation_name: str): return None
    def _can_spend(_nation_name: str, _cost: dict) -> bool: return True
    def _spend(_nation_name: str, _cost: dict) -> bool: return True
    def _load_nation(_nation_name: str): return {"자원": {}, "자원한도": {}}

_EMOJI = {"돈":"💰","식량":"🍚","연료":"⛽","징집병":"🪖"}
def _fmt_res_line(res: dict, caps: dict) -> str:
    if not isinstance(res, dict): res = {}
    if not isinstance(caps, dict): caps = {}
    keys = ["돈","식량","연료","징집병"] + [k for k in res.keys() if k not in {"돈","식량","연료","징집병"}]
    parts=[]
    for k in keys:
        if k not in res and k not in caps: continue
        v = int(res.get(k,0))
        cap = caps.get(k)
        parts.append(f"{_EMOJI.get(k,'•')} {k} {v:,}{'/' + str(cap) if cap is not None else ''}")
    return "  |  ".join(parts) if parts else "(자원 데이터 없음)"

# -------------------- 이동 UI --------------------
class MoveSelect(discord.ui.Select):
    def __init__(self, current_coord: str, options_coords: List[str]):
        options = [discord.SelectOption(label=c, value=c) for c in options_coords]
        super().__init__(placeholder="이동할 좌표를 선택하세요", options=options[:25], min_values=1, max_values=1)
        self.current_coord = current_coord

    async def callback(self, interaction: discord.Interaction):
        try:
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
            except Exception:
                pass

            target = self.values[0]

            if target == "이동불가":
                await interaction.followup.send("⛔ 이 칸에서 이동 가능한 인접 지역이 없습니다.", ephemeral=True)
                return

            fpath = user_path(interaction.user.id)
            data = load_json(fpath, {}) or {}
            if not data:
                await interaction.followup.send("❌ 먼저 `/가입`을 해주세요!", ephemeral=True); return

            cur = data.get("location")
            if not is_valid_coord(cur):
                await interaction.followup.send("⚠️ 현재 위치 정보가 올바르지 않습니다. 관리자에게 위치 초기화를 요청하세요.", ephemeral=True)
                return

            if not is_valid_coord(target) or not is_passable(target):
                await interaction.followup.send("⛔ 해당 좌표로는 이동할 수 없습니다.", ephemeral=True)
                return

            reservations = data.get("reservations", [])
            if len(reservations) >= MAX_RESERVATIONS:
                await interaction.followup.send(f"⚠️ 이미 {MAX_RESERVATIONS}개의 행동이 예약되어 있어요.", ephemeral=True)
                return

            reservations.append({
                "action": f"이동 {target}",
                "type": "이동",
                "from": cur,         # 원점 기록
                "target": target
            })
            data["reservations"] = reservations
            save_json(fpath, data)  # ✅ 저장 누락 보완

            await interaction.followup.send(
                f"📦 `{cur}` → `{target}` 이동이 예약되었습니다! ({len(reservations)}/{MAX_RESERVATIONS})",
                ephemeral=True
            )
        except Exception:
            traceback.print_exc()
            try:
                await interaction.followup.send("⚠️ 이동 처리 중 오류가 발생했습니다.", ephemeral=True)
            except Exception:
                pass

class MoveView(discord.ui.View):
    def __init__(self, current_coord: str, grid):
        super().__init__(timeout=300)
        if not is_valid_coord(current_coord):
            candidates = ["이동불가"]
        else:
            x, y = LETTERS.index(current_coord[0].upper()), int(current_coord[1:]) - 1
            candidates: List[str] = []
            for dx, dy in [(0,1),(0,-1),(1,0),(-1,0)]:
                nx, ny = x+dx, y+dy
                if 0 <= nx < MAP_WIDTH and 0 <= ny < MAP_HEIGHT:
                    coord = f"{LETTERS[nx]}{ny+1}"
                    if is_passable(coord, grid):
                        candidates.append(coord)
            if not candidates:
                candidates = ["이동불가"]
        self.add_item(MoveSelect(current_coord, candidates[:25]))

# -------------------- 모집 UI --------------------
class RecruitSelect(discord.ui.Select):
    def __init__(self, recruitables: List[Dict]):
        options = [discord.SelectOption(label=r["name"], value=r["name"]) for r in recruitables]
        super().__init__(placeholder="모집할 병종을 선택하세요", options=options[:25], min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        try:
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
            except Exception:
                pass

            unit_name = self.values[0]
            fpath = user_path(interaction.user.id)
            data = load_json(fpath, {}) or {}
            if not data:
                await interaction.followup.send("❌ 먼저 `/가입`을 해주세요!", ephemeral=True); return

            # 슬롯 확인
            units = data.get("units", [])
            if len(units) >= slot_limit(data):
                data["pending_recruitment"] = {"병종": unit_name, "최대인구": 500}
                save_json(fpath, data)
                await interaction.followup.send("📦 슬롯이 가득 찼습니다. 다음 빈 슬롯에 자동 모집으로 전환했습니다.", ephemeral=True)
                return

            # 유닛 정의/비용
            season = get_season_number()
            unit_defs = load_json(get_unit_defs_path(season), {}) or {}
            uinfo = unit_defs.get(unit_name, {}) or {}
            max_pop = int(uinfo.get("인구", 500))

            nation_name = data.get("nation") or "무소속"
            _ensure_account(nation_name)

            start_pop = 100
            recruit_cost = {}
            raw_cost = (uinfo.get("모집비용") or {})
            for k, v in (raw_cost.items() if isinstance(raw_cost, dict) else []):
                try:
                    recruit_cost[k] = int(v)
                except Exception:
                    pass
            # 시작 병력만큼 징집병(및 원하면 식량) 소모
            recruit_cost["징집병"] = recruit_cost.get("징집병", 0) + start_pop
            # recruit_cost["식량"] = recruit_cost.get("식량", 0) + start_pop

            # 자원 확인/차감
            if recruit_cost and not _can_spend(nation_name, recruit_cost):
                ndata = _load_nation(nation_name) or {}
                res = ndata.get("자원", {}) or {}
                caps = ndata.get("자원한도", {}) or {}
                pretty_need = ", ".join(f"{k}:{v}" for k, v in recruit_cost.items())
                pretty_have = _fmt_res_line(res, caps)
                await interaction.followup.send(
                    f"💸 `{unit_name}` 모집 실패: 자원이 부족합니다.\n"
                    f"• 필요: {pretty_need}\n"
                    f"• 현재: {pretty_have}",
                    ephemeral=True
                )
                return

            if recruit_cost and not _spend(nation_name, recruit_cost):
                await interaction.followup.send("⚠️ 모집 도중 자원 차감에 실패했습니다. 잠시 후 다시 시도하세요.", ephemeral=True); return

            # 실제 모집
            units.append({"이름": unit_name, "병종": unit_name, "인구": start_pop, "최대인구": max_pop})
            data["units"] = units
            save_json(fpath, data)

            await interaction.followup.send(f"🪖 `{unit_name}` 부대를 모집했습니다! (인구 {start_pop} 시작)", ephemeral=True)
        except Exception:
            traceback.print_exc()
            try:
                await interaction.followup.send("⚠️ 모집 처리 중 오류가 발생했습니다.", ephemeral=True)
            except Exception:
                pass

class RecruitView(discord.ui.View):
    def __init__(self, recruitables: List[Dict]):
        super().__init__(timeout=300)
        if not recruitables:
            opts = [discord.SelectOption(label="모집 가능한 병종이 없습니다.", value="none")]
            sel = discord.ui.Select(placeholder="모집 불가", options=opts, disabled=True)
            self.add_item(sel)
        else:
            self.add_item(RecruitSelect(recruitables))

# -------------------- 코그 --------------------
class UIActions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="이동ui", description="드롭다운으로 인접 지역 이동을 예약합니다.")
    async def 이동ui(self, interaction: discord.Interaction):
        fpath = user_path(interaction.user.id)
        data = load_json(fpath, {}) or {}
        if not data:
            await interaction.response.send_message("❌ 먼저 `/가입`을 해주세요!", ephemeral=True); return

        cur = data.get("location")
        if not cur or not is_valid_coord(cur):
            await interaction.response.send_message("❌ 현재 위치 정보를 찾을 수 없습니다.", ephemeral=True); return

        grid = load_map_grid()
        if not isinstance(grid, dict):
            await interaction.response.send_message("❌ 맵 데이터가 없습니다. 관리자에게 문의해주세요.", ephemeral=True); return

        view = MoveView(cur, grid)
        await interaction.response.send_message("📍 이동할 지역을 선택하세요:", view=view, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(UIActions(bot))

    async def _cleanup_and_sync_after_ready():
        await bot.wait_until_ready()
        try:
            # 1) 전역 트리에 남아 있을 수 있는 오래된 커맨드 제거 (이름은 'positional-only')
            stale = ("모집ui", "건설ui")
            removed = []
            for n in stale:
                try:
                    bot.tree.remove_command(n)  # ← name= 아닌 위치 인자!
                    removed.append(n)
                except Exception as e:
                    print(f"[ui_actions] remove '{n}' skipped: {e}")

            # 2) 전역 동기화
            try:
                await bot.tree.sync()
                if removed:
                    print(f"[ui_actions] removed {', '.join(removed)} (global) and synced.")
            except Exception as e:
                print(f"[ui_actions] global sync failed: {e}")

            # 3) 길드별에도 혹시 남아 있으면 정리(예전 코드에서 guild 커맨드로 썼던 경우 대비)
            for g in list(bot.guilds):
                try:
                    await bot.tree.sync(guild=g)
                except Exception as e:
                    print(f"[ui_actions] guild sync failed for {getattr(g, 'name', g.id)}: {e}")

        except Exception as e:
            print(f"[ui_actions] cleanup skip: {e}")

    try:
        bot.loop.create_task(_cleanup_and_sync_after_ready())
    except Exception as e:
        print(f"[ui_actions] schedule cleanup failed: {e}")
