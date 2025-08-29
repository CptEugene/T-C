# commands/ui_map.py
import discord, os, json, io, math, traceback
from discord.ext import commands
from services.scheduler import queue_action

DATA_DIR = "data"
REGION_FILE   = os.path.join(DATA_DIR, "region_data.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "game_settings.json")
CITYMAP_FILE  = os.path.join(DATA_DIR, "city_map.json")

# ----- 공용 I/O -----
def _load(p, d=None):
    if not os.path.exists(p): return d
    with open(p, "r", encoding="utf-8") as f: return json.load(f)

def _save(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, ensure_ascii=False)

def _user(uid): return os.path.join(DATA_DIR, f"{uid}.json")

def _load_settings():
    return _load(SETTINGS_FILE, {}) or {}

def _load_region():
    return _load(REGION_FILE, {}) or {}

# ----- 국가/수도/매핑 -----
try:
    from services.nations import CAPITALS, NATION_DATA
    NAME2ID = { (v or {}).get("name"): k for k, v in (NATION_DATA or {}).items() }
except Exception:
    CAPITALS, NATION_DATA, NAME2ID = {}, {}, {}

def _owner_id(owner_raw):
    if not owner_raw: return None
    if owner_raw in (NATION_DATA or {}): return owner_raw
    return NAME2ID.get(owner_raw)

# ----- Pillow -----
try:
    from PIL import Image, ImageDraw, ImageFont
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

# ====== 이미지 그리기 유틸 ======
def _hash_color(nation_id: str):
    r = sum(ord(ch) for ch in nation_id) % 256
    g = (sum(ord(ch)*3 for ch in nation_id) // 2) % 256
    b = (sum(ord(ch)*7 for ch in nation_id) // 3) % 256
    if r+g+b < 180: r, g, b = r+60, g+60, b+60
    return (min(r,255), min(g,255), min(b,255))

def _color_for_owner(owner_id):
    if not owner_id: return (90,180,100)  # neutral
    preset = {
        "RED": (220,60,60), "BLUE": (70,110,230), "YELLOW": (235,200,60),
        "GREEN": (60,190,120), "PURPLE": (150,90,220)
    }
    return preset.get(owner_id, _hash_color(owner_id))

def _text(draw, pos, text, font, fill=(235,235,240), stroke=(20,20,25), sw=2):
    try:
        draw.text(pos, text, font=font, fill=fill, stroke_fill=stroke, stroke_width=sw)
    except TypeError:
        draw.text(pos, text, font=font, fill=fill)

def _draw_star(draw, cx, cy, r, fill):
    pts = []
    for i in range(10):
        ang = math.radians(i*36 - 90)
        rad = r if i % 2 == 0 else r*0.45
        pts.append((cx + rad*math.cos(ang), cy + rad*math.sin(ang)))
    draw.polygon(pts, fill=fill)

def _draw_flag_rect(draw, x, y, w, h, color):
    # 작은 깃발: 좌측 위 모서리에 붙임
    draw.rounded_rectangle([x, y, x+w, y+h], radius=2, fill=color, outline=(25,25,30))
    draw.line([x-2, y-2, x-2, y+h+2], fill=(30,30,35), width=2)

def _load_icon(path, fallback_size=(28, 24), fallback_color=(230,220,210), roof_color=(215,120,70)):
    """PNG가 있으면 RGBA로, 없으면 성 모양 벡터를 그린 작은 이미지 반환"""
    if path and os.path.exists(path):
        img = Image.open(path).convert("RGBA")
        return img
    # fallback vector
    w, h = fallback_size
    img = Image.new("RGBA", (w, h), (0,0,0,0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 6, w, h], radius=4, fill=fallback_color, outline=(30,30,35), width=2)
    roof = [(0,6), (w,6), (w//2, 0)]
    d.polygon(roof, fill=roof_color, outline=(30,30,35))
    return img

# ====== city_map.json 템플릿(없으면 자동 생성) ======
def _ensure_citymap_template(region: dict):
    if os.path.exists(CITYMAP_FILE): return
    # 간단 템플릿: 그리드 좌표를 도시로, 상하/좌우 인접을 루트로
    coords = sorted([c for c in region.keys() if len(c) >= 2 and c[1:].isdigit()],
                    key=lambda s: (s[0], int(s[1:])))
    if not coords:
        tmpl = {"background": "", "size": [1400, 900], "cities": {}, "routes": []}
        _save(CITYMAP_FILE, tmpl); return

    letters = sorted({c[0].upper() for c in coords})
    rows    = sorted({int(c[1:]) for c in coords})
    W, H = len(letters), len(rows)

    def norm_x(letter): return (letters.index(letter) + 0.5) / max(W,1)
    def norm_y(rowno):  return (rows.index(int(rowno)) + 0.5) / max(H,1)

    cities = {}
    for c in coords:
        x = norm_x(c[0].upper())
        y = norm_y(c[1:])
        cities[c] = {"pos": [x, y], "coord": c, "capital": (CAPITALS and any(v == c for v in CAPITALS.values()))}

    routes = []
    for c in coords:
        x_i = letters.index(c[0].upper()); y_i = rows.index(int(c[1:]))
        if x_i+1 < W: routes.append([c, f"{letters[x_i+1]}{rows[y_i]}"])
        if y_i+1 < H: routes.append([c, f"{letters[x_i]}{rows[y_i+1]}"])

    tmpl = {
        "background": "",           # 원하면 배경 이미지 경로(예: "data/worldmap_bg.png")
        "size": [1400, 900],
        "icons": {                  # PNG 아이콘 경로(없으면 자동 벡터)
            "city":    "data/icons/city.png",
            "capital": "data/icons/capital.png"
        },
        "cities": cities,
        "routes": routes
    }
    _save(CITYMAP_FILE, tmpl)

# ====== 메인 렌더: 삼국지식 도시 지도(이미지 전용) ======
def _render_city_map_image(region: dict) -> io.BytesIO | None:
    cfg = _load(CITYMAP_FILE, None)
    if not cfg: return None
    if not HAVE_PIL: return None

    Wimg, Himg = (cfg.get("size") or [1400, 900])
    bg_path = cfg.get("background")
    try:
        if bg_path and os.path.exists(bg_path):
            bg = Image.open(bg_path).convert("RGBA").resize((Wimg, Himg))
        else:
            bg = Image.new("RGBA", (Wimg, Himg), (22,24,28,255))
    except Exception:
        bg = Image.new("RGBA", (Wimg, Himg), (22,24,28,255))

    draw = ImageDraw.Draw(bg)
    try:
        font = ImageFont.truetype("arial.ttf", 18)
        font_city = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font = ImageFont.load_default(); font_city = font

    # 아이콘 로드(없으면 벡터 fallback)
    icon_city    = _load_icon((cfg.get("icons") or {}).get("city"))
    icon_capital = _load_icon((cfg.get("icons") or {}).get("capital"), fallback_size=(36,28))

    cities = cfg.get("cities", {})
    routes = cfg.get("routes", [])

    # 위치 헬퍼
    def P(name):
        info = cities.get(name) or {}
        px, py = info.get("pos", [0.5, 0.5])
        return (int(px*Wimg), int(py*Himg))

    # 도로(밝은 본선 + 어두운 외곽선)
    for a, b in routes:
        if a not in cities or b not in cities: continue
        ax, ay = P(a); bx, by = P(b)
        draw.line([ax, ay, bx, by], fill=(25,25,30), width=12)     # 외곽
        draw.line([ax, ay, bx, by], fill=(232,176,96), width=6)    # 본선

    # 도시 아이콘 + 이름 + 깃발(점령색) + 수도표시
    for name, info in cities.items():
        px, py = info.get("pos", [0.5, 0.5])
        x, y = int(px*Wimg), int(py*Himg)
        coord = info.get("coord")      # region 칸 연결
        is_capital = bool(info.get("capital")) \
                     or (CAPITALS and any(v == coord for v in CAPITALS.values()))

        # 소속(점령) 색
        owner_raw = (region.get(coord, {}) or {}).get("owner") if coord else None
        owner = _owner_id(owner_raw)
        flag_color = _color_for_owner(owner)

        # 아이콘(수도/일반) 붙이기 — center anchor
        icon = icon_capital if is_capital else icon_city
        iw, ih = icon.size
        bg.alpha_composite(icon, (x - iw//2, y - ih//2))

        # 깃발: 아이콘 좌상단
        _draw_flag_rect(draw, x - iw//2 - 14, y - ih//2 - 18, 18, 12, flag_color)

        # 수도 별(아이콘 우상단)
        if is_capital:
            _draw_star(draw, x + iw//2 + 10, y - ih//2 - 10, 8, (255,230,100))

        # 도시 이름
        _text(draw, (x - (len(name)*8)//2, y + ih//2 + 4), name, font_city)

    # 범례(좌하단)
    _text(draw, (16, Himg-26), "★ 수도 • 깃발색 = 점령국 • 노선 = 통행로", font)

    buf = io.BytesIO(); bg.save(buf, format="PNG"); buf.seek(0)
    return buf

# ====== UI: 드롭다운(이미지 전용) ======
# 이동 드롭다운은 기존 그리드 규칙을 따르므로 그대로 유지
def _valid_coord(c: str, region: dict):
    if not isinstance(c, str) or len(c) < 2: return False
    if not c[1:].isdigit(): return False
    return c in region

def _neighbors(coord: str, region: dict):
    """상하좌우 인접만 허용(region에 존재하는 좌표만)"""
    letters = sorted({k[0].upper() for k in region.keys() if len(k)>=2 and k[1:].isdigit()})
    rows    = sorted({int(k[1:]) for k in region.keys() if len(k)>=2 and k[1:].isdigit()})
    if not letters or not rows: return []
    x, y = letters.index(coord[0].upper()), rows.index(int(coord[1:]))
    res = []
    for dx, dy in [(0,1),(0,-1),(1,0),(-1,0)]:
        nx, ny = x+dx, y+dy
        if 0 <= nx < len(letters) and 0 <= ny < len(rows):
            cand = f"{letters[nx]}{rows[ny]}"
            if cand in region: res.append(cand)
    return res

try:
    from .ui_actions import MoveSelect as _MoveSelect
    HAVE_UI = True
except Exception:
    HAVE_UI = False
    _MoveSelect = None

class MoveContainerView(discord.ui.View):
    def __init__(self, current_loc: str, region: dict):
        super().__init__(timeout=300)
        if not _valid_coord(current_loc, region):
            candidates = ["이동불가"]
        else:
            candidates = _neighbors(current_loc, region) or ["이동불가"]

        if HAVE_UI and _MoveSelect:
            self.add_item(_MoveSelect(current_loc, candidates[:25]))
        else:
            sel = discord.ui.Select(
                placeholder="이동 불가 (모듈 없음)",
                options=[discord.SelectOption(label="이동불가", value="x")],
                disabled=True
            )
            self.add_item(sel)

        from .ui_menu import BackHome
        self.add_item(BackHome())

class MapSelect(discord.ui.Select):
    def __init__(self):
        opts = [
            discord.SelectOption(label="인접 이동(도시 지도 참조)", value="adj"),
            discord.SelectOption(label="도시 지도(삼국지식)", value="romance")
        ]
        super().__init__(placeholder="지도 기능을 선택하세요", options=opts, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        from .ui_menu import build_tab_embed
        try:
            await interaction.response.defer(ephemeral=True, thinking=False)
        except Exception:
            pass

        try:
            choice = self.values[0]
            user = _load(_user(interaction.user.id), {})
            if not user:
                embed = build_tab_embed("map", "❌ 먼저 `/가입`을 해주세요!")
                await interaction.edit_original_response(content=None, embed=embed, view=self.view); return

            region = _load_region()
            if not isinstance(region, dict) or not region:
                embed = build_tab_embed("map", "❌ 맵 데이터가 없습니다.")
                await interaction.edit_original_response(content=None, embed=embed, view=self.view); return

            # city_map 템플릿 보장
            _ensure_citymap_template(region)

            if not HAVE_PIL:
                embed = build_tab_embed("map", "⚠️ Pillow가 필요합니다", "서버에 `pip install pillow` 설치 후 다시 시도하세요.")
                await interaction.edit_original_response(content=None, embed=embed, view=self.view); return

            img_buf = _render_city_map_image(region)
            if not img_buf:
                embed = build_tab_embed("map", "⚠️ 렌더 실패", "`city_map.json`을 확인하세요.")
                await interaction.edit_original_response(content=None, embed=embed, view=self.view); return

            file = discord.File(img_buf, filename="citymap.png")

            if choice == "romance":
                embed = build_tab_embed("map", "🏯 도시 지도(삼국지식)", "도시/루트/점령국을 시각화한 지도입니다.")
                embed.set_image(url="attachment://citymap.png")
                await interaction.edit_original_response(content=None, embed=embed, attachments=[file], view=self.view); return

            if choice == "adj":
                my_loc = user.get("location") if _valid_coord(user.get("location"), region) else None
                desc = "📍 인접 지역을 선택해 이동 예약:"
                embed = build_tab_embed("map", "🗺️ 인접 이동", desc + "\n(아래 이미지를 참조하세요)")
                embed.set_image(url="attachment://citymap.png")
                view = MoveContainerView(my_loc, region) if HAVE_UI else self.view
                await interaction.edit_original_response(content=None, embed=embed, attachments=[file], view=view); return

        except Exception as e:
            traceback.print_exc()
            try:
                await interaction.followup.send(f"⚠️ 처리 중 오류가 발생했어요: `{type(e).__name__}`", ephemeral=True)
            except Exception:
                pass

class MapView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(MapSelect())
        from .ui_menu import BackHome
        self.add_item(BackHome())

# ✅ ui_menu가 import하는 엔트리
def map_embed() -> discord.Embed:
    from .ui_menu import build_tab_embed
    desc = "원하는 기능을 선택하세요: **인접 이동(도시 지도 참조)** · **도시 지도(삼국지식)**"
    return build_tab_embed("map", "월드맵", desc)

class UIMapCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

async def setup(bot: commands.Bot):
    await bot.add_cog(UIMapCog(bot))
