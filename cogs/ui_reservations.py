# commands/ui_reservations.py
import os, json, traceback
import discord
from discord.ext import commands

DATA_DIR = "data"
MAX_RES = 4
def _user(uid): return os.path.join(DATA_DIR, f"{uid}.json")
def _load(p, d=None):
    if not os.path.exists(p): return d
    try:
        with open(p, "r", encoding="utf-8") as f: return json.load(f)
    except Exception:
        return d
def _save(p, data):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f: json.dump(data, f, indent=4, ensure_ascii=False)

def _list_lines(rsv: list[dict]) -> str:
    if not rsv: return "(예약 없음)"
    out = []
    for i, r in enumerate(rsv, 1):
        name = r.get("action") or r.get("type","?")
        extra = []
        if "target" in r: extra.append(f"→ {r['target']}")
        if "turns_left" in r: extra.append(f"{r['turns_left']}턴")
        out.append(f"{i}. {name} {' '.join(extra)}")
    return "\n".join(out)

class CancelSelect(discord.ui.Select):
    def __init__(self, rsv: list[dict]):
        options = []
        for i, r in enumerate(rsv[:25], 1):
            label = r.get("action") or r.get("type","?")
            desc = (f"idx {i}" + (f" / {r.get('target')}" if r.get('target') else ""))
            options.append(discord.SelectOption(label=label, value=str(i-1), description=desc))
        super().__init__(placeholder="취소할 예약을 선택하세요", options=options, min_values=1, max_values=1)
        self._source = rsv
    async def callback(self, interaction: discord.Interaction):
        try:
            f = _user(interaction.user.id)
            data = _load(f, {}) or {}
            rsv = data.get("reservations", []) or []
            idx = int(self.values[0])
            if not (0 <= idx < len(rsv)):
                await interaction.response.send_message("⚠️ 유효하지 않은 선택입니다.", ephemeral=True); return
            removed = rsv.pop(idx)
            data["reservations"] = rsv; _save(f, data)
            await interaction.response.send_message(f"🗑️ 예약 취소: {removed.get('action') or removed.get('type')}", ephemeral=True)
        except Exception:
            traceback.print_exc()
            try: await interaction.response.send_message("⚠️ 예약 취소 중 오류", ephemeral=True)
            except: pass

class ClearAll(discord.ui.Button):
    def __init__(self):
        super().__init__(label="전체 취소", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def callback(self, interaction: discord.Interaction):
        try:
            f = _user(interaction.user.id)
            data = _load(f, {}) or {}
            data["reservations"] = []
            _save(f, data)
            await interaction.response.send_message("🧹 모든 예약을 취소했습니다.", ephemeral=True)
        except Exception:
            traceback.print_exc()
            try: await interaction.response.send_message("⚠️ 전체 취소 중 오류", ephemeral=True)
            except: pass

class ReservationsView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        f = _user(user_id)
        data = _load(f, {}) or {}
        self.rsv = data.get("reservations", []) or []
        if self.rsv:
            self.add_item(CancelSelect(self.rsv))
            self.add_item(ClearAll())

def reservations_embed(user_id: int) -> discord.Embed:
    from .ui_menu import build_embed
    f = _user(user_id)
    data = _load(f, {}) or {}
    rsv = data.get("reservations", []) or []
    return build_embed("rsv", "예약 확인/취소", _list_lines(rsv))

class UIReservations(commands.Cog):
    def __init__(self, bot): self.bot = bot

async def setup(bot: commands.Bot):
    await bot.add_cog(UIReservations(bot))
