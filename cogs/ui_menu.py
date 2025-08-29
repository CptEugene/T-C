# cogs/ui_menu.py
# /메뉴 → 집무실(홈). 버튼: 내정, 군사, 이동, 예약, 인사, 턴결과
import os, json, importlib, traceback, inspect
import discord
from discord.ext import commands
from discord import app_commands

DATA_DIR = "data"
USER_FEED_DIR = os.path.join(DATA_DIR, "user_feed")
SETTINGS_FILE = os.path.join(DATA_DIR, "game_settings.json")

# ====== 탭별 에셋 이미지 경로 (프로젝트 루트 기준) ======
# 파일이 존재하지 않으면 해당 탭은 이미지 없이 전송됩니다.
TAB_ASSETS = {
    "home": "assets/image/로고.png",
    "army": "assets/image/배너.png",
    "turn": "assets/image/턴시간.png",
    "int":  "assets/image/정보.png",
    "move": "assets/image/지도.png",
    "hr":   "assets/image/정보.png",   # 인사 탭
    "rsv":  "assets/image/턴시간.png", # 예약 탭(임시)
}

# ================== 공용 유틸 ==================
def _load(p, d=None):
    if not os.path.exists(p): return d
    with open(p, "r", encoding="utf-8") as f: return json.load(f)

def _user(uid): return os.path.join(DATA_DIR, f"{uid}.json")
def _settings(): return _load(SETTINGS_FILE, {}) or {}
def _user_feed(uid): return _load(os.path.join(USER_FEED_DIR, f"{uid}.json"), {}) or {}

try:
    from services.economy import ensure_account as _ensure_account, load_nation as _econ_load_nation
except Exception:
    def _ensure_account(_): return None
    def _econ_load_nation(_): return {}

def _nation_res(nation_name: str):
    try:
        _ensure_account(nation_name)
        snap = _econ_load_nation(nation_name) or {}
        return snap.get("자원", {}) or {}, snap.get("자원한도", {}) or {}
    except Exception:
        return {}, {}

def _fmt_num(n):
    try: return f"{int(n):,}"
    except: return str(n)

def _fmt_res(res: dict, caps: dict) -> str:
    keys = ["돈","식량","연료","징집병"] + [k for k in res.keys() if k not in {"돈","식량","연료","징집병"}]
    parts=[]
    for k in keys:
        if k not in res and k not in caps: continue
        v = res.get(k, 0); cap = caps.get(k)
        parts.append(f"{k} {_fmt_num(v)}" + (f"/{_fmt_num(cap)}" if cap is not None else ""))
    return " | ".join(parts) if parts else "(자원 정보 없음)"

def _get_tab_asset(tab: str) -> str | None:
    path = TAB_ASSETS.get(tab)
    return path if (path and os.path.exists(path)) else None

# ================== 안전/유연 임포트 ==================
def _import_mod(modname: str):
    """cogs.modname 우선 로드. 실패시 __package__ 보조."""
    tried = []
    try:
        return importlib.import_module(f"cogs.{modname}")
    except Exception as e1:
        tried.append(("cogs." + modname, e1))
        try:
            pkg = __package__ or "cogs"
            return importlib.import_module(f"{pkg}.{modname}")
        except Exception as e2:
            tried.append((f"{pkg}.{modname}", e2))
            print(f"[ui_menu] import fail: {modname}")
            for name, err in tried:
                print(" - tried:", name, "=>", repr(err))
            traceback.print_exc()
            raise

def _load_attr(modname: str, attr: str):
    """모듈에서 정확한 심볼명을 가져옵니다."""
    mod = _import_mod(modname)
    return getattr(mod, attr)

def _load_view_flexible(modname: str, prefer: tuple[str, ...], default_view_cls: type[discord.ui.View] | None = None):
    """
    View 심볼명이 달라도 최대한 찾아 반환:
    1) prefer 목록 순서대로 getattr 시도
    2) 모듈 내에서 discord.ui.View 서브클래스 첫 번째를 자동 탐색
    3) 없으면 default_view_cls 또는 간단한 플레이스홀더 뷰 반환
    """
    mod = _import_mod(modname)
    for name in prefer:
        if hasattr(mod, name):
            cls = getattr(mod, name)
            if inspect.isclass(cls) and issubclass(cls, discord.ui.View):
                return cls

    for _, obj in inspect.getmembers(mod, inspect.isclass):
        if issubclass(obj, discord.ui.View) and obj is not discord.ui.View:
            return obj

    class _FallbackView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)
            self.add_item(discord.ui.Button(label="View를 찾지 못했습니다", style=discord.ButtonStyle.gray, disabled=True))
    return default_view_cls or _FallbackView

def _load_embed_builder_flexible(modname: str, prefer: tuple[str, ...], tab_key: str):
    """
    Embed 빌더 함수명이 달라도 최대한 찾아 반환:
    1) prefer 이름 순서대로 getattr 시도
    2) 시그니처로 추정: (user_id) 하나 받는 함수
    3) 없으면 해당 탭용 기본 embed 빌더를 반환
    """
    mod = _import_mod(modname)
    for name in prefer:
        if hasattr(mod, name) and callable(getattr(mod, name)):
            return getattr(mod, name)

    for _, obj in inspect.getmembers(mod, inspect.isfunction):
        try:
            sig = inspect.signature(obj)
            params = list(sig.parameters.values())
            if len(params) == 1:
                return obj
        except Exception:
            pass

    # fallback: 단순 안내 임베드
    def _fallback_embed(user_id: int):
        titles = {
            "army": "군사",
            "int":  "내정",
            "move": "이동",
            "hr":   "인사",
            "rsv":  "예약",
        }
        return build_tab_embed(tab_key, titles.get(tab_key, "정보"), f"{modname} 모듈의 임베드 생성 함수를 찾지 못했습니다.")
    return _fallback_embed

# ================== Embed 빌더/송신 ==================
TAB_ICONS = {"home":"🏠","army":"🪖","turn":"⏱️","int":"🏗️","move":"🧭","hr":"🧩","rsv":"📅"}

def build_tab_embed(tab: str, title: str, desc: str | None = None):
    color_map = {
        "home": discord.Color.blurple(),
        "army": discord.Color.red(),
        "turn": discord.Color.gold(),
        "int":  discord.Color.gold(),
        "move": discord.Color.blue(),
        "hr":   discord.Color.green(),
        "rsv":  discord.Color.purple(),
    }
    icon = TAB_ICONS.get(tab, "📎")
    return discord.Embed(
        title=f"{icon} {title}",
        description=desc or "",
        color=color_map.get(tab, discord.Color.dark_gray())
    )

async def _send_with_asset(interaction: discord.Interaction, *, embed: discord.Embed, view: discord.ui.View, tab: str, initial: bool):
    """
    에셋 이미지를 embed에 첨부해서 보냄.
    - initial=True  : interaction.response.send_message(embed, file=..., ephemeral=True)
    - initial=False : interaction.response.defer(ephemeral=True) 후 interaction.followup.send(embed, file=..., ephemeral=True)
      (edit_message는 새 파일 첨부가 불가하므로 새 에페메럴 메시지로 보냄)
    파일이 없으면 이미지 없이 전송.
    """
    path = _get_tab_asset(tab)
    if initial:
        if path:
            filename = os.path.basename(path)
            file = discord.File(path, filename=filename)
            embed.set_image(url=f"attachment://{filename}")
            await interaction.response.send_message(embed=embed, view=view, file=file, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.defer(ephemeral=True)
        if path:
            filename = os.path.basename(path)
            file = discord.File(path, filename=filename)
            embed.set_image(url=f"attachment://{filename}")
            await interaction.followup.send(embed=embed, view=view, file=file, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

# ========= 홈 임베드 (아바타 + 스탯/지역/부대/국가) =========
def build_home_embed(user_id: int, display_name: str, avatar_url: str | None = None):
    u = _load(_user(user_id), {}) or {}
    name = display_name or "지휘관"

    # 기본
    lv  = int(u.get("level", 1))
    xp  = int(u.get("exp", 0))
    sp  = int(u.get("stat_points", 0))
    nation = u.get("nation", "무소속")
    loc    = u.get("location", "미배치")

    # 스탯 (통솔/무력/지력/매력)
    st = (u.get("stats") or {})
    s_t = int(st.get("통솔", 0))
    s_m = int(st.get("무력", 0))
    s_j = int(st.get("지력", 0))
    s_a = int(st.get("매력", 0))
    stats_line = f"통솔 {s_t} / 무력 {s_m} / 지력 {s_j} / 매력 {s_a}"

    # 부대
    units = u.get("units", []) or []
    total_pop = sum(int(x.get("인구", 0) or 0) for x in units)
    unit_brief = f"{len(units)}개 / 병력 {_fmt_num(total_pop)}"

    # 국가 자원
    res, caps = _nation_res(nation)
    res_line = _fmt_res(res, caps)

    # 메인 임베드 (썸네일=프로필)
    emb = build_tab_embed("home", f"{name}님의 집무실")
    if avatar_url:
        emb.set_thumbnail(url=avatar_url)

    # 정보 필드
    emb.add_field(name="📍 기본", value=f"소속 **{nation}**\n위치 **{loc}**\n레벨 **{lv}** (XP {xp})", inline=True)
    emb.add_field(name="🧬 스탯", value=f"{stats_line}\n남은 포인트 **{sp}**", inline=True)
    emb.add_field(name="🪖 전력", value=f"{unit_brief}", inline=True)

    # 구분선 후 자원 + 예약
    emb.add_field(name="\u200b", value="\u200b", inline=False)
    emb.add_field(name="💰 국가 자원", value=res_line, inline=False)

    rsv = u.get("reservations", []) or []
    if rsv:
        short = "\n".join(f"• {r.get('action') or (r.get('type','?') + ' 예약')}" for r in rsv[:5])
        if len(rsv) > 5: short += f"\n… 외 {len(rsv)-5}개"
        emb.add_field(name="🗓️ 예약", value=short, inline=False)
    else:
        emb.add_field(name="🗓️ 예약", value="(없음)", inline=False)

    return emb

# ===================== 뷰/버튼 ======================
class BackHome(discord.ui.Button):
    def __init__(self): super().__init__(label="홈으로", style=discord.ButtonStyle.secondary)
    async def callback(self, interaction: discord.Interaction):
        emb = build_home_embed(
            interaction.user.id,
            interaction.user.display_name,
            str(interaction.user.display_avatar.url)
        )
        await _send_with_asset(interaction, embed=emb, view=HomeView(), tab="home", initial=False)

class HomeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        # 지침 카테고리만 노출 (집무실/지도 버튼 제거)
        self.add_item(InternalButton())
        self.add_item(ArmyButton())
        self.add_item(MoveButton())
        self.add_item(PersonnelButton())
        self.add_item(ReservButton())
        self.add_item(TurnButton())

# === 내정 ===
class InternalButton(discord.ui.Button):
    def __init__(self): super().__init__(label="내정", style=discord.ButtonStyle.primary, emoji="🏗️")
    async def callback(self, interaction: discord.Interaction):
        try:
            InternalView = _load_view_flexible(
                "ui_internal",
                prefer=("InternalView","InternalMainView","InternalHomeView","Internal")
            )
            # 내정은 임베드 빌더가 없을 수도 있으니 기본 문구
            emb = build_tab_embed("int", "내정", "건설 / 특수")
            await _send_with_asset(interaction, embed=emb, view=InternalView(), tab="int", initial=False)
        except Exception:
            emb = build_tab_embed("int", "내정", "⚠️ 내정 모듈 로드 오류")
            await _send_with_asset(interaction, embed=emb, view=HomeView(), tab="int", initial=False)

# === 군사 ===
class ArmyButton(discord.ui.Button):
    def __init__(self): super().__init__(label="군사", style=discord.ButtonStyle.danger, emoji="🪖")
    async def callback(self, interaction: discord.Interaction):
        try:
            ArmyViewCls = _load_view_flexible(
                "ui_army",
                prefer=("ArmyView","ArmyMainView","ArmyHomeView","ArmyRootView","Army")
            )
            army_embed_builder = _load_embed_builder_flexible(
                "ui_army",
                prefer=("army_home_embed","army_embed","build_army_embed"),
                tab_key="army"
            )
            emb = army_embed_builder(interaction.user.id)
            await _send_with_asset(interaction, embed=emb, view=ArmyViewCls(), tab="army", initial=False)
        except Exception:
            emb = build_tab_embed("army", "군사", "⚠️ 군사 모듈 로드 오류")
            await _send_with_asset(interaction, embed=emb, view=HomeView(), tab="army", initial=False)

# === 이동 (지도 포함) ===
class MoveButton(discord.ui.Button):
    def __init__(self): super().__init__(label="이동", style=discord.ButtonStyle.secondary, emoji="🧭")
    async def callback(self, interaction: discord.Interaction):
        try:
            MoveMainView = _load_view_flexible(
                "ui_move",
                prefer=("MoveMainView","MoveView","MoveHomeView","Move")
            )
            emb = build_tab_embed("move", "이동", "이동 / 수송 (지도 포함)")
            await _send_with_asset(interaction, embed=emb, view=MoveMainView(interaction.user.id), tab="move", initial=False)
        except Exception:
            emb = build_tab_embed("move", "이동", "⚠️ 이동 모듈 로드 오류")
            await _send_with_asset(interaction, embed=emb, view=HomeView(), tab="move", initial=False)

# === 인사 ===
class PersonnelButton(discord.ui.Button):
    def __init__(self): super().__init__(label="인사", style=discord.ButtonStyle.success, emoji="🧩")
    async def callback(self, interaction: discord.Interaction):
        try:
            PersonnelView = _load_view_flexible(
                "ui_personnel",
                prefer=("PersonnelView","HRView","PersonnelMainView","Personnel")
            )
            personnel_embed = _load_embed_builder_flexible(
                "ui_personnel",
                prefer=("personnel_embed","build_personnel_embed"),
                tab_key="hr"
            )
            emb = personnel_embed(interaction.user.id)
            await _send_with_asset(interaction, embed=emb, view=PersonnelView(), tab="hr", initial=False)
        except Exception:
            emb = build_tab_embed("hr", "인사", "⚠️ 인사 모듈 로드 오류")
            await _send_with_asset(interaction, embed=emb, view=HomeView(), tab="hr", initial=False)

# === 예약 ===
class ReservButton(discord.ui.Button):
    def __init__(self): super().__init__(label="예약", style=discord.ButtonStyle.success, emoji="📅")
    async def callback(self, interaction: discord.Interaction):
        try:
            ReservationsView = _load_view_flexible(
                "ui_reservations",
                prefer=("ReservationsView","ReserveView","ReservationView","Reservations")
            )
            reservations_embed = _load_embed_builder_flexible(
                "ui_reservations",
                prefer=("reservations_embed","build_reservations_embed"),
                tab_key="rsv"
            )
            emb = reservations_embed(interaction.user.id)
            await _send_with_asset(interaction, embed=emb, view=ReservationsView(interaction.user.id), tab="rsv", initial=False)
        except Exception:
            emb = build_tab_embed("rsv", "예약", "⚠️ 예약 모듈 로드 오류")
            await _send_with_asset(interaction, embed=emb, view=HomeView(), tab="rsv", initial=False)

# === 턴결과 ===
class TurnButton(discord.ui.Button):
    def __init__(self): super().__init__(label="턴결과", style=discord.ButtonStyle.success, emoji="⏱️")
    async def callback(self, interaction: discord.Interaction):
        emb = build_tab_embed("turn", "턴 결과", "전체 요약 또는 내 결과를 확인하세요.")
        await _send_with_asset(interaction, embed=emb, view=TurnResultView(), tab="turn", initial=False)

# ============= 턴결과 보조 =============
def _fmt_battle_list(feed: dict) -> str:
    if not feed: return "기록이 없습니다."
    battles = feed.get("battles", [])
    if not battles: return "기록이 없습니다."
    return "\n".join(f"• {b}" for b in battles[:30])

def _fmt_personal(feed: dict) -> tuple[str, str, str]:
    if not feed: return ("이번 턴 개인 결과가 없습니다.", "전투 기록이 없습니다.", "전투 대사 기록이 없습니다.")
    events = feed.get("events", []); battles = feed.get("battles", []); logs = feed.get("battle_logs", [])
    e_txt = "\n".join(f"• {e}" for e in events) if events else "이번 턴 개인 결과가 없습니다."
    b_txt = "\n".join(f"• {b}" for b in battles) if battles else "전투 기록이 없습니다."
    l_txt = ("\n".join(logs[:30]) + ("\n…(더 있음)" if len(logs) > 30 else "")) if logs else "전투 대사 기록이 없습니다."
    return (e_txt, b_txt, l_txt)

class TurnResultView(discord.ui.View):
    def __init__(self): super().__init__(timeout=300); self.add_item(BackHome())
    @discord.ui.button(label="전체 요약", style=discord.ButtonStyle.primary)
    async def show_global(self, interaction: discord.Interaction, _):
        s = _settings(); turn = int(s.get("current_turn", 0))
        feed = _load(os.path.join(DATA_DIR, "turn_feed.json"), {}) or {}
        battles = feed.get("battles", [])
        btxt = "\n".join(
            f"• {b.get('coord', b.get('region','?'))} {b.get('kind','?')} ({' vs '.join(b.get('nations',[]))})"
            for b in battles[:20]
        ) if battles else "이번 턴 전투 없음"
        emb = build_tab_embed("turn", f"턴 {turn} 결과 - 전체 요약", btxt)
        await _send_with_asset(interaction, embed=emb, view=self, tab="turn", initial=False)

    @discord.ui.button(label="내 결과", style=discord.ButtonStyle.success)
    async def show_personal(self, interaction: discord.Interaction, _):
        s = _settings(); turn = int(s.get("current_turn", 0))
        feed = _user_feed(interaction.user.id)
        ev, bl, logs = _fmt_personal(feed)
        emb = build_tab_embed("turn", f"👤 내 턴 결과 (턴 {turn})",
                              f"**행동 요약**\n{ev}\n\n**전투 요약**\n{bl}\n\n**전투 대사**\n{logs}")
        await _send_with_asset(interaction, embed=emb, view=self, tab="turn", initial=False)

# ============= Cog 등록 =============
class UIMenuCog(commands.Cog):
    def __init__(self, bot): self.bot = bot
    @commands.Cog.listener()
    async def on_ready(self): print("[ui_menu] ready")

    @app_commands.command(name="메뉴", description="집무실 메뉴를 엽니다.")
    async def 메뉴(self, interaction: discord.Interaction):
        emb = build_home_embed(
            interaction.user.id,
            interaction.user.display_name,
            str(interaction.user.display_avatar.url)
        )
        await _send_with_asset(interaction, embed=emb, view=HomeView(), tab="home", initial=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(UIMenuCog(bot))
