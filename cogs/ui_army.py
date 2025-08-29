# commands/ui_army.py
import os, json, traceback
import discord
from discord.ext import commands

DATA_DIR = "data"
SETTINGS_FILE = os.path.join(DATA_DIR, "game_settings.json")
REGION_FILE = os.path.join(DATA_DIR, "region_data.json")
MAX_RES = 4
LETTERS="ABCDE"; W=5; H=5

def _load(p,d=None):
    if not os.path.exists(p): return d
    with open(p,"r",encoding="utf-8") as f: import json; return json.load(f)
def _save(p,data):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p,"w",encoding="utf-8") as f: import json; json.dump(data,f,indent=4,ensure_ascii=False)
def _user(uid): return os.path.join(DATA_DIR, f"{uid}.json")
def _season():
    s=_load(SETTINGS_FILE,{}) or {}
    try: return int(s.get("season_number",1))
    except: return 1
def _unit_defs():
    return _load(os.path.join(DATA_DIR, f"units_season{_season()}.json"), {}) or {}
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
def _slot_limit(user):
    stats=user.get("stats",{}) or {}
    return 4 + int(stats.get("통솔",0))

# 경제 유틸
try:
    from services.economy import ensure_account, can_spend, spend, load_nation
except Exception:
    def ensure_account(_): ...
    def can_spend(_n,_c): return True
    def spend(_n,_c): return True
    def load_nation(_): return {"자원": {}, "자원한도": {}}

def army_summary_embed(user_id:int) -> discord.Embed:
    u=_load(_user(user_id),{}) or {}
    units=u.get("units",[]) or []; tot=sum(int(x.get("인구",0) or 0) for x in units)
    lines=[f"• {x.get('이름') or x.get('병종') or '부대'} ({int(x.get('인구',0) or 0):,}/{int(x.get('최대인구',0) or 0):,})" for x in units[:10]]
    if len(units)>10: lines.append(f"… 외 {len(units)-10}개")
    desc=f"보유 부대: **{len(units)}개** / 총 병력 **{tot:,}**\n" + ("\n".join(lines) if lines else "(부대 없음)")
    return discord.Embed(title="🪖 군사: 모집·훈련·출진", description=desc, color=discord.Color.red())

def _fmt_res_line(res,caps):
    keys=["돈","식량","연료","징집병"] + [k for k in res.keys() if k not in {"돈","식량","연료","징집병"}]
    parts=[]
    for k in keys:
        if k not in res and k not in caps: continue
        v=int(res.get(k,0)); cap=caps.get(k)
        parts.append(f"{k} {v:,}{'/' + str(cap) if cap is not None else ''}")
    return "  |  ".join(parts) if parts else "(자원 데이터 없음)"

# ── 모집 ──
class RecruitSelect(discord.ui.Select):
    def __init__(self, defs):
        ops=[]
        for name, info in list(defs.items())[:25]:
            pop=int(info.get("인구",500)); atk=info.get("공격력",10)
            cost=info.get("모집비용",{}) or {}
            cpretty=", ".join(f"{k}:{v}" for k,v in cost.items()) if cost else "비용 없음"
            ops.append(discord.SelectOption(label=name, value=name, description=f"ATK {atk} / 인구{pop} / {cpretty}"))
        super().__init__(placeholder="모집할 병종 선택", options=ops, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        try:
            unit_name=self.values[0]
            f=_user(interaction.user.id)
            data=_load(f,{}) or {}
            if not data:
                await interaction.response.send_message("❌ 먼저 `/가입`을 해주세요!", ephemeral=True); return
            units=data.get("units",[])
            if len(units) >= _slot_limit(data):
                data["pending_recruitment"]={"병종":unit_name,"최대인구":500}
                _save(f,data)
                await interaction.response.send_message("📦 슬롯이 가득 찼습니다. 빈 슬롯에 자동 모집으로 전환했습니다.", ephemeral=True); return

            defs=_unit_defs(); uinfo=defs.get(unit_name,{}) or {}
            max_pop=int(uinfo.get("인구",500)); nation=data.get("nation") or "무소속"; ensure_account(nation)
            start_pop=100
            raw_cost=uinfo.get("모집비용") or {}; cost={k:int(v) for k,v in raw_cost.items()} if isinstance(raw_cost,dict) else {}
            cost["징집병"]=cost.get("징집병",0)+start_pop

            if cost and not can_spend(nation, cost):
                ndata=load_nation(nation) or {}; res=ndata.get("자원",{}) or {}; caps=ndata.get("자원한도",{}) or {}
                await interaction.response.send_message(f"💸 `{unit_name}` 모집 실패: 자원이 부족합니다.\n• 필요: {', '.join(f'{k}:{v}' for k,v in cost.items())}\n• 현재: {_fmt_res_line(res,caps)}", ephemeral=True); return

            if cost and not spend(nation, cost):
                await interaction.response.send_message("⚠️ 모집 도중 자원 차감 실패. 잠시 후 재시도하세요.", ephemeral=True); return

            units.append({"이름":unit_name,"병종":unit_name,"인구":start_pop,"최대인구":max_pop})
            data["units"]=units; _save(f,data)
            await interaction.response.send_message(f"🪖 `{unit_name}` 부대를 모집했습니다! (인구 {start_pop} 시작)", ephemeral=True)
        except Exception:
            traceback.print_exc()
            try: await interaction.response.send_message("⚠️ 모집 처리 중 오류", ephemeral=True)
            except: pass

# ── 훈련 ──
class TrainBtn(discord.ui.Button):
    def __init__(self): super().__init__(label="훈련 예약", style=discord.ButtonStyle.success, emoji="📗")
    async def callback(self, interaction: discord.Interaction):
        f=_user(interaction.user.id); data=_load(f,{}) or {}
        if not data: await interaction.response.send_message("❌ 먼저 `/가입`을 해주세요!", ephemeral=True); return
        rsv=data.get("reservations",[])
        if len(rsv)>=MAX_RES: await interaction.response.send_message("⚠️ 이미 4개 예약이 있습니다.", ephemeral=True); return
        rsv.append({"type":"훈련","action":"훈련"}); data["reservations"]=rsv; _save(f,data)
        await interaction.response.send_message(f"📗 훈련을 예약했습니다! ({len(rsv)}/{MAX_RES})", ephemeral=True)

# ── 출진 ──
class SortieSelect(discord.ui.Select):
    def __init__(self, choices):
        ops=[discord.SelectOption(label=c, value=c) for c in (choices or ["none"])]
        super().__init__(placeholder="출진할 인접 지역 선택", options=ops[:25], min_values=1, max_values=1)
    async def callback(self, interaction: discord.Interaction):
        target=self.values[0]
        if target=="none":
            await interaction.response.send_message("⛔ 인접 지역 없음", ephemeral=True); return
        f=_user(interaction.user.id); data=_load(f,{}) or {}
        if not data: await interaction.response.send_message("❌ 먼저 `/가입`을 해주세요!", ephemeral=True); return
        cur=data.get("location")
        if not _is_valid_coord(cur): await interaction.response.send_message("⚠️ 현재 위치 오류", ephemeral=True); return
        rsv=data.get("reservations",[])
        if len(rsv)>=MAX_RES: await interaction.response.send_message("⚠️ 이미 4개 예약이 있습니다.", ephemeral=True); return
        rsv.append({"type":"이동","action":f"출진 {target}","from":cur,"target":target,"attack_intent":True})
        data["reservations"]=rsv; _save(f,data)
        await interaction.response.send_message(f"🗡️ `{cur}` → `{target}` 출진(이동) 예약 완료! ({len(rsv)}/{MAX_RES})", ephemeral=True)

class MilitaryView(discord.ui.View):
    def __init__(self, user_id:int):
        super().__init__(timeout=300)
        # 모집
        defs=_unit_defs()
        if defs:
            self.add_item(RecruitSelect(defs))
        else:
            sel = discord.ui.Select(placeholder="모집 데이터 없음", options=[discord.SelectOption(label="데이터 없음", value="none")], disabled=True)
            self.add_item(sel)
        # 훈련 버튼
        self.add_item(TrainBtn())
        # 출진 셀렉트
        grid=_load(REGION_FILE,{})
        u=_load(_user(user_id),{}) or {}
        cur=u.get("location")
        choices=_adj(cur,grid) if isinstance(grid,dict) else []
        self.add_item(SortieSelect(choices))
        from .ui_menu import BackHome
        self.add_item(BackHome())

class UIArmy(commands.Cog):
    def __init__(self, bot): self.bot=bot

async def setup(bot: commands.Bot):
    await bot.add_cog(UIArmy(bot))
