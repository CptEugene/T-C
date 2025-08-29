# commands/map.py
# 슬래시 명령어 제거 버전
# - 관리자만 사용 가능한 버튼/UI로 동기화 실행
import os, json, traceback
import discord
from discord.ext import commands

MAP_PATH = "data/map.json"
REGION_PATH = "data/region_data.json"

OWNER_EMOJI = {None:"🟩","중립":"🟩","RED":"🟥","BLUE":"🟦"}
CAPITAL_EMOJI = "⭐"

def _load(p):
    if not os.path.exists(p): return None
    try:
        with open(p,"r",encoding="utf-8") as f: return json.load(f)
    except: return None

def _save(p, data):
    with open(p,"w",encoding="utf-8") as f: json.dump(data,f,indent=2,ensure_ascii=False)

def _detect_decorated_layout(grid):
    if not grid or not isinstance(grid, list) or not grid[0]: return False
    header_row = grid[0]
    return any(cell in {"🅰️","🅱️","🅲️","🅳️","🅴️"} for cell in header_row)

def _coord_to_index_basic(coord):
    letter = coord[0].upper()
    num = int(coord[1:])
    x = ord(letter)-ord("A"); y = num-1
    return x,y

def _coord_to_index_decorated(coord):
    letter = coord[0].upper()
    num = int(coord[1:])
    x = 2*(ord(letter)-ord("A"))+1
    y = 2*(num-1)+1
    return x,y

def _set(grid, x, y, val):
    if 0 <= y < len(grid) and 0 <= x < len(grid[0]):
        grid[y][x] = val; return True
    return False

class MapSyncView(discord.ui.View):
    def __init__(self): super().__init__(timeout=180)

    @discord.ui.button(label="맵 타일 동기화", style=discord.ButtonStyle.danger, emoji="🧭")
    async def sync(self, interaction: discord.Interaction, _):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⚠️ 관리자만 사용할 수 있어요.", ephemeral=True); return
        try:
            grid = _load(MAP_PATH)
            region = _load(REGION_PATH)
            if grid is None:
                await interaction.response.send_message("❌ map.json이 없습니다. 먼저 생성하세요.", ephemeral=True); return
            if not isinstance(grid, list) or not grid or not isinstance(grid[0], list):
                await interaction.response.send_message("❌ map.json 형식이 2차원 배열이 아닙니다.", ephemeral=True); return
            if not isinstance(region, dict) or not region:
                await interaction.response.send_message("❌ region_data.json을 찾을 수 없거나 비어 있습니다.", ephemeral=True); return

            decorated = _detect_decorated_layout(grid)
            changed = 0
            for coord, slot in region.items():
                if len(coord)<2 or not coord[0].isalpha() or not coord[1:].isdigit():
                    continue
                try:
                    x,y = (_coord_to_index_decorated(coord) if decorated else _coord_to_index_basic(coord))
                except:
                    continue
                owner = slot.get("owner")
                capital = bool(slot.get("capital"))
                base_emoji = OWNER_EMOJI.get(owner, OWNER_EMOJI[None])
                tile = CAPITAL_EMOJI if capital else base_emoji
                if _set(grid, x, y, tile):
                    changed += 1
            _save(MAP_PATH, grid)
            await interaction.response.send_message(f"🧭 맵 타일 동기화 완료! 갱신된 칸: **{changed}**", ephemeral=True)
        except Exception:
            traceback.print_exc()
            try: await interaction.response.send_message("⚠️ 동기화 중 오류가 발생했습니다.", ephemeral=True)
            except: pass

class MapCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

async def setup(bot: commands.Bot):
    await bot.add_cog(MapCog(bot))
