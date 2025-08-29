# commands/ui_move.py
import os, json
import discord
from discord.ext import commands

DATA_DIR="data"; REGION_FILE=os.path.join(DATA_DIR,"region_data.json")
LETTERS="ABCDE"; W=5; H=5; MAX_RES=4
def _user(uid): return os.path.join(DATA_DIR,f"{uid}.json")
def _load(p,d=None):
    if not os.path.exists(p): return d
    with open(p,"r",encoding="utf-8") as f: import json; return json.load(f)

def _is_valid_coord(c):
    if not c or len(c)<2 or not c[0].isalpha() or not c[1:].isdigit(): return False
    x=LETTERS.find(c[0].upper()); y=int(c[1:])-1
    return 0<=x<W and 0<=y<H
def _adj(cur,grid):
    if not _is_valid_coord(cur): return []
    x=LETTERS.find(cur[0].upper()); y=int(cur[1:])-1
    out=[]
    for dx,dy in [(0,1),(0,-1),(1,0),(-1,0)]:
        nx,ny=x+dx,y+dy
        if 0<=nx<W and 0<=ny<H:
            code=f"{LETTERS[nx]}{ny+1}"
            if isinstance(grid,dict) and code in grid: out.append(code)
    return out

class _BaseMoveSelect(discord.ui.Select):
    KIND="이동"
    def __init__(self, choices):
        ops=[discord.SelectOption(label=c, value=c) for c in (choices or ["none"])]
        super().__init__(placeholder=f"{self.KIND}할 인접 지역 선택", options=ops[:25], min_values=1, max_values=1)
    async def callback(self, interaction: discord.Interaction):
        target=self.values[0]
        if target=="none":
            await interaction.response.send_message("⛔ 인접 지역 없음", ephemeral=True); return
        f=_user(interaction.user.id); 
        if not os.path.exists(f):
            await interaction.response.send_message("❌ 먼저 `/가입`을 해주세요!", ephemeral=True); return
        with open(f,"r",encoding="utf-8") as rf: data=json.load(rf)
        cur=data.get("location")
        if not _is_valid_coord(cur): await interaction.response.send_message("⚠️ 현재 위치 오류", ephemeral=True); return
        rsv=data.get("reservations",[])
        if len(rsv)>=MAX_RES: await interaction.response.send_message("⚠️ 이미 4개 예약이 있습니다.", ephemeral=True); return
        rsv.append({"type":self.KIND, "action": f"{self.KIND} {target}", "from":cur, "target":target})
        data["reservations"]=rsv
        with open(f,"w",encoding="utf-8") as wf: json.dump(data,wf,indent=4,ensure_ascii=False)
        await interaction.response.send_message(f"✅ `{cur}` → `{target}` {self.KIND} 예약 완료! ({len(rsv)}/{MAX_RES})", ephemeral=True)

class MoveSelect(_BaseMoveSelect): KIND="이동"
class ConvoySelect(_BaseMoveSelect): KIND="수송"

class MoveMainView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        grid=_load(REGION_FILE,{})
        with open(_user(user_id),"r",encoding="utf-8") as f:
            u=json.load(f)
        cur=u.get("location")
        choices=_adj(cur,grid) if isinstance(grid,dict) else []
        self.add_item(MoveSelect(choices))
        self.add_item(ConvoySelect(choices))
        from .ui_menu import BackHome
        self.add_item(BackHome())

class UIMove(commands.Cog):
    def __init__(self, bot): self.bot=bot
async def setup(bot: commands.Bot):
    await bot.add_cog(UIMove(bot))
