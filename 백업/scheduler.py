# commands/reserve.py
import discord  # 공지 채널 송출을 위해 추가
from discord import app_commands
from discord.ext import commands
import os, json, asyncio, random, re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

# ✅ combat 위치에 따라 유연하게 import (commands/ 내부 또는 프로젝트 루트)
try:
    from .combat import 전투처리  # combat.py가 commands/ 안에 있을 때
except ImportError:
    from combat import 전투처리   # combat.py가 프로젝트 루트에 있을 때

# ✅ 경제 모듈 (buildings.json 기반)
try:
    from .economy import tick_production, building_cost, spend, ensure_account, can_spend, load_nation
except Exception:
    # economy 모듈이 없으면 최소 더미(런타임 오류 방지)
    def tick_production(_region): return {}
    def building_cost(_name): return {}
    def spend(_nation, _cost): return True
    def ensure_account(_nation): return None
    def can_spend(_nation, _cost): return True
    def load_nation(_nation): return {"자원": {"돈": 10**9, "식량": 10**9, "연료": 10**9, "징집병": 10**9}}

MAX_RESERVATIONS = 4
KST = timezone(timedelta(hours=9))
DATA_DIR = "data"
SETTINGS_FILE = os.path.join(DATA_DIR, "game_settings.json")
REGION_PATH = os.path.join(DATA_DIR, "region_data.json")
MAP_PATH = os.path.join(DATA_DIR, "map.json")

# ◀️ UI가 읽는 턴결과 파일들
TURN_FEED_FILE = os.path.join(DATA_DIR, "turn_feed.json")     # 전체 요약
USER_FEED_DIR  = os.path.join(DATA_DIR, "user_feed")          # 개인 결과 폴더

# 좌표계(필요 시 맵 크기 조정)
LETTERS = "ABCDEFG"
MAP_WIDTH = 7
MAP_HEIGHT = 9


class Reserve(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.loop.create_task(self.start_turn_loop())

    # -------------------- 공용 유틸 --------------------
    def load_settings(self) -> dict:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "season_number": 1,
            "auto_rest": {"enabled": True, "min": 15, "max": 35},
            "auto_recruit_from_pending": True,
        }

    def save_settings(self, settings: dict):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)

    def load_unit_defs(self, season_number: int) -> dict:
        path = os.path.join(DATA_DIR, f"units_season{season_number}.json")
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    ATTACK_TYPE_BY_CLASS = {
        "검병": "돌격", "창병": "돌격", "방패병": "전열", "장창병": "전열",
        "궁병": "사격", "화승총병": "사격", "석궁병": "사격",
        "기병": "기동", "경기병": "기동", "중기병": "돌격",
        "전투기": "기동", "뇌격기": "기동", "함선": "전열"
    }

    def to_attack_type(self, unit_name: str, unit_defs: dict) -> str:
        info = unit_defs.get(unit_name, {})
        troop_class = info.get("병종유형") or info.get("병종")
        if troop_class in self.ATTACK_TYPE_BY_CLASS:
            return self.ATTACK_TYPE_BY_CLASS[troop_class]
        if "전투기" in unit_name or "뇌격기" in unit_name:
            return "기동"
        return "전열"

    def build_side_from_players(self, player_ids: List[str], unit_defs: Dict[str, dict], user_datas: Dict[str, dict]):
        combat_units = []
        for uid in player_ids:
            pdata = user_datas.get(uid)
            if not pdata or not isinstance(pdata, dict):
                continue
            commander = pdata.get("commander_name", uid)
            for u in pdata.get("units", []) or []:
                name = u.get("이름")
                if not name:
                    continue
                base = unit_defs.get(name, {})
                atk = int(base.get("공격력", 10))
                pop = max(0, int(u.get("인구", 0)))
                if pop <= 0:
                    continue
                combat_units.append({
                    "이름": name,
                    "플레이어": commander,
                    "공격력": atk,
                    "방어력": int(base.get("방어력", 0)),
                    "공격유형": self.to_attack_type(name, unit_defs),
                    "체력": pop,                 # 체력=현재 인구
                    "표기방식": base.get("표기방식", "병력"),
                    "_owner_id": uid
                })
        return combat_units

    def apply_losses_to_users(self, combat_list: List[dict], user_datas: Dict[str, dict]):
        """전투 종료 후 combat_list의 '체력(남은 인구)'를 유저 데이터에 반영"""
        from collections import defaultdict
        left_sum = defaultdict(int)  # (uid, name) -> 남은 인구 총합
        for cu in combat_list:
            left_sum[(cu.get("_owner_id"), cu.get("이름"))] += max(0, int(cu.get("체력", 0)))

        for uid, pdata in user_datas.items():
            if not isinstance(pdata, dict):
                continue
            name_left = {name: left for (u, name), left in left_sum.items() if u == uid}
            new_units = []
            for u in pdata.get("units", []) or []:
                name = u.get("이름")
                if not name:
                    continue
                cur_left = int(name_left.get(name, 0))
                if cur_left <= 0:
                    continue
                keep = min(int(u.get("인구", 0)), cur_left)
                u["인구"] = keep
                name_left[name] = max(0, cur_left - keep)
                if u["인구"] > 0:
                    new_units.append(u)
            pdata["units"] = new_units

    # -------------------- ★ PvE(중립) 전용 헬퍼 --------------------
    def build_side_from_neutral(self, neutral_list: List[dict], unit_defs: dict) -> List[dict]:
        """region_slot['neutral_units'] -> 전투 엔진 입력 포맷으로 변환"""
        side = []
        for nu in (neutral_list or []):
            name = nu.get("이름")
            pop  = int(nu.get("인구", 0))
            if not name or pop <= 0:
                continue
            base = unit_defs.get(name, {})
            side.append({
                "이름": name,
                "플레이어": "중립",
                "공격력": int(base.get("공격력", 10)),
                "방어력": int(base.get("방어력", 10)),
                "공격유형": self.to_attack_type(name, unit_defs),
                "체력": pop,
                "표기방식": base.get("표기방식", "병력"),
                "_neutral": True,
            })
        return side

    def apply_losses_to_neutral(self, slot: dict, neutral_combat_list: List[dict]):
        """전투 후 중립 병력 잔여 인구를 region_slot에 반영"""
        left, total = [], 0
        for cu in neutral_combat_list:
            name = cu.get("이름")
            hp   = max(0, int(cu.get("체력", 0)))
            if name and hp > 0:
                left.append({"이름": name, "인구": hp})
                total += hp
        if left:
            slot["neutral_units"] = left
            slot["neutral_total"] = total
            slot["unit_size"] = total
            slot["enemy_presence"] = True
        else:
            slot.pop("neutral_units", None)
            slot["neutral_total"] = 0
            slot["unit_size"] = 0
            slot["enemy_presence"] = False

    def try_capture_slot(self, coord: str, slot: dict, users: Dict[str, dict]) -> Optional[str]:
        """
        점령 조건:
          - 중립 수비 없음
          - 이 칸에 살아있는 유저가 있고
          - 그 유저들의 nation이 모두 동일
        성립 시 owner 변경, 국가명 반환
        """
        if slot.get("neutral_units") or int(slot.get("neutral_total", 0) or 0) > 0:
            return None
        occupants = [uid for uid in (slot.get("units") or []) if uid in users]
        if not occupants:
            return None
        alive = []
        for uid in occupants:
            if any(int(u.get("인구", 0)) > 0 for u in (users[uid].get("units") or [])):
                alive.append(uid)
        if not alive:
            return None
        nations = set((users[uid].get("nation") or "무소속") for uid in alive)
        nations.discard(None)
        if len(nations) != 1:
            return None
        new_owner = list(nations)[0]
        if slot.get("owner") != new_owner:
            slot["owner"] = new_owner
            return new_owner
        return None

    def ensure_region_schema(self):
        """unit(단수) -> units(복수) 마이그레이션"""
        if not os.path.exists(REGION_PATH):
            return
        try:
            with open(REGION_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        changed = False
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            if "units" not in v:
                v["units"] = []
                changed = True
            if v.get("unit"):
                uid = str(v["unit"])
                if uid not in v["units"]:
                    v["units"].append(uid)
                v["unit"] = None
                changed = True
        if changed:
            with open(REGION_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    # -------------------- 좌표/이동 유틸 --------------------
    def is_valid_coord(self, coord: str) -> bool:
        if len(coord) < 2:
            return False
        x, y = coord[0].upper(), coord[1:]
        if x not in LETTERS or not y.isdigit():
            return False
        xi, yi = LETTERS.index(x), int(y) - 1
        return 0 <= xi < MAP_WIDTH and 0 <= yi < MAP_HEIGHT

    def adjacent(self, a: str, b: str) -> bool:
        ax, ay = LETTERS.index(a[0]), int(a[1:]) - 1
        bx, by = LETTERS.index(b[0]), int(b[1:]) - 1
        return (abs(ax - bx) + abs(ay - by)) == 1  # 상하좌우 1칸

    def load_region(self) -> dict:
        if not os.path.exists(REGION_PATH):
            return {}
        try:
            with open(REGION_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    # ✅ “리건데이터에 좌표가 있어야 이동 가능”
    def passable(self, coord: str) -> bool:
        region = self.load_region()
        return coord in region

    # -------------------- 슬롯/모집 유틸(자동 충원) --------------------
    def _slot_limit(self, user: dict) -> int:
        전략 = int(user.get("stats", {}).get("전략", 0))
        return 4 + 전략

    def _auto_fill_from_pending(self, user: dict) -> Optional[str]:
        """슬롯 여유 + pending_recruitment 있으면 자동 충원"""
        recruit = user.get("pending_recruitment")
        if not recruit:
            return None
        units = user.setdefault("units", [])
        if len(units) >= self._slot_limit(user):
            return None
        new_unit = {
            "이름": recruit["병종"],
            "병종": recruit["병종"],
            "인구": 100,
            "최대인구": int(recruit.get("최대인구", 500))
        }
        units.append(new_unit)
        user.pop("pending_recruitment", None)
        return new_unit["이름"]

    # -------------------- ✏️ 턴결과 저장 헬퍼 --------------------
    def _init_feeds(self, users: Dict[str, dict]):
        user_events = {uid: [] for uid in users.keys()}
        user_battles = {uid: [] for uid in users.keys()}
        global_battles: List[dict] = []
        return user_events, user_battles, global_battles

    def _append_user_event(self, user_events: Dict[str, List[str]], uid: str, text: str):
        user_events.setdefault(uid, []).append(text)

    def _append_user_battle(self, user_battles: Dict[str, List[str]], participants: List[str], text: str):
        for uid in participants:
            user_battles.setdefault(uid, []).append(text)

    def _extract_battle_result(self, logs: List[str]) -> Optional[str]:
        # combat 로그에서 "✅ 전투 종료! 결과: ..." 라인을 찾아 요약
        for line in reversed(logs or []):
            m = re.search(r"결과:\s*([^\n]+)", line)
            if m:
                return m.group(1).strip()
        return None

    def _save_feeds(self, turn: int, global_battles: List[dict], user_events: Dict[str, List[str]], user_battles: Dict[str, List[str]]):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(TURN_FEED_FILE, "w", encoding="utf-8") as f:
                json.dump({"turn": turn, "battles": global_battles[:200]}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ turn_feed 저장 실패: {e}")

        try:
            os.makedirs(USER_FEED_DIR, exist_ok=True)
            for uid in set(user_events.keys()) | set(user_battles.keys()):
                payload = {
                    "turn": turn,
                    "events": user_events.get(uid, [])[:100],
                    "battles": user_battles.get(uid, [])[:100]
                }
                with open(os.path.join(USER_FEED_DIR, f"{uid}.json"), "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ user_feed 저장 실패: {e}")

    # -------------------- 맵타일 자동 동기화 --------------------
    def _detect_decorated_layout(self, grid: List[List[str]]) -> bool:
        if not grid or not isinstance(grid, list) or not grid[0]:
            return False
        header_row = grid[0]
        return any(cell in {"🅰️", "🅱️", "🅲️", "🅳️", "🅴️"} for cell in header_row)

    def _coord_to_index_basic(self, coord: str) -> Tuple[int, int]:
        letter = coord[0].upper()
        num = int(coord[1:])
        x = ord(letter) - ord("A")
        y = num - 1
        return x, y

    def _coord_to_index_decorated(self, coord: str) -> Tuple[int, int]:
        letter = coord[0].upper()
        num = int(coord[1:])
        x_idx = 2 * (ord(letter) - ord("A")) + 1
        y_idx = 2 * (num - 1) + 1
        return x_idx, y_idx

    def _sync_map_tiles(self, region_data: dict, settings: dict):
        if not os.path.exists(MAP_PATH):
            return
        try:
            with open(MAP_PATH, "r", encoding="utf-8") as f:
                grid = json.load(f)
        except Exception:
            return
        if not isinstance(grid, list) or not grid or not isinstance(grid[0], list):
            return

        # 이모지 설정(설정 값 우선)
        ntile = settings.get("nation_tiles", {"RED": "🟥", "BLUE": "🟦"})
        ncap  = settings.get("nation_capitals", {"RED": "⭐", "BLUE": "☆"})
        neutral_tile = settings.get("neutral_tile", "🟩")

        decorated = self._detect_decorated_layout(grid)
        changed = 0

        for coord, slot in (region_data or {}).items():
            if not isinstance(slot, dict) or len(coord) < 2 or not coord[0].isalpha() or not coord[1:].isdigit():
                continue
            try:
                x, y = (self._coord_to_index_decorated(coord) if decorated else self._coord_to_index_basic(coord))
            except Exception:
                continue

            owner = slot.get("owner")
            capital = bool(slot.get("capital"))
            base_emoji = ntile.get(owner, neutral_tile)
            tile_emoji = ncap.get(owner, "⭐") if capital else base_emoji

            if 0 <= y < len(grid) and 0 <= x < len(grid[0]):
                grid[y][x] = tile_emoji
                changed += 1

        try:
            with open(MAP_PATH, "w", encoding="utf-8") as f:
                json.dump(grid, f, indent=2, ensure_ascii=False)
            if changed:
                print(f"[reserve] 맵 타일 동기화 완료: {changed}칸 업데이트")
        except Exception as e:
            print(f"[reserve] 맵 타일 저장 실패: {e}")

    # -------------------- 전투 요약(채널 공지) 빌더 --------------------
    @staticmethod
    def _count_types(units: List[dict]) -> Dict[str, int]:
        counter = {"전열": 0, "기동": 0, "사격": 0, "돌격": 0}
        for u in units or []:
            t = u.get("공격유형")
            if t in counter:
                counter[t] += 1
        return counter

    @staticmethod
    def _fmt_types(counter: Dict[str, int]) -> str:
        return f"전열 {counter['전열']}, 기동 {counter['기동']}, 사격 {counter['사격']}, 돌격 {counter['돌격']}"

    async def _announce_turn(self, turn: int, summaries: List[str], settings: dict):
        ch_id = settings.get("announce_channel")
        if not ch_id:
            return
        channel = self.bot.get_channel(int(ch_id))
        if not channel:
            return

        header = f"⏱️ **턴 {turn}** 결과"
        lines = [header] + (summaries or ["세상이 평화롭습니다."])
        text = "\n".join(lines)

        # Discord 2000자 제한 대비 간단 분할
        chunks = []
        while len(text) > 1900:
            cut = text.rfind("\n", 0, 1900)
            if cut == -1: cut = 1900
            chunks.append(text[:cut])
            text = text[cut:].lstrip("\n")
        if text:
            chunks.append(text)

        for i, chunk in enumerate(chunks):
            try:
                await channel.send(chunk)
            except Exception as e:
                print(f"[reserve] 공지 송출 실패: {e}")
                break

    # -------------------- 턴 루프 --------------------
    def cog_unload(self):
        pass

    async def start_turn_loop(self):
        self.ensure_region_schema()  # 최초 마이그레이션
        while True:
            now = datetime.now(KST)
            minute_blocks = [0, 15, 30, 45]
            next_minute = next((m for m in minute_blocks if now.minute < m), 60)
            next_turn_time = now.replace(minute=next_minute % 60, second=0, microsecond=0)
            if next_minute == 60:
                next_turn_time += timedelta(hours=1)
            await asyncio.sleep((next_turn_time - now).total_seconds())
            await self.process_turn()

    async def process_turn(self):
        # 설정 로드/턴 증가
        settings = self.load_settings()
        current_turn = int(settings.get("current_turn", 0)) + 1
        settings["current_turn"] = current_turn
        self.save_settings(settings)

        turn_summaries: List[str] = []      # (내부 로그용)
        battle_logs: List[str] = []         # (내부 로그용)
        announce_lines: List[str] = []      # 🔔 채널 공지용 줄들
        # ▶️ 턴결과(요약/개인) 누적 버퍼
        user_events, user_battles, global_battles = self._init_feeds({})

        # === 시스템/유저 파일 로드 ===
        def is_user_json(filename: str) -> bool:
            if not filename.endswith(".json"):
                return False
            name = filename[:-5]
            return name.isdigit()  # 숫자 파일명만 유저로 간주(국가/시스템 JSON 제외)

        users: Dict[str, dict] = {}
        os.makedirs(DATA_DIR, exist_ok=True)
        for filename in os.listdir(DATA_DIR):
            if not is_user_json(filename):
                continue
            try:
                with open(os.path.join(DATA_DIR, filename), "r", encoding="utf-8") as f:
                    pdata = json.load(f)
            except Exception:
                continue
            if isinstance(pdata, dict):
                users[filename.replace(".json", "")] = pdata

        # ▶️ 유저 로딩 후 피드 버퍼 초기화
        user_events, user_battles, global_battles = self._init_feeds(users)

        # ==== 지역 로드 ====
        if not os.path.exists(REGION_PATH):
            print("❌ region_data.json 없음")
            return
        with open(REGION_PATH, "r", encoding="utf-8") as f:
            region_data = json.load(f)

        # 유저 현재 좌표 인덱스(역인덱스)
        reverse_index = {}
        if isinstance(region_data, dict):
            for coord, slot in region_data.items():
                if not isinstance(slot, dict):
                    continue
                for uid in slot.get("units", []) or []:
                    reverse_index[str(uid)] = coord

        # ==== 1) 🏭 국가 생산 ====
        prod = tick_production(region_data)  # {"알티스 공화국":{"돈":..., "식량":...}, ...}
        for n, delta in (prod or {}).items():
            pretty = ", ".join(f"{k}+{v}" for k, v in delta.items())
            turn_summaries.append(f"🏭 **{n}** 생산: {pretty}")

        # ==== 2) 🌿 자동회복 적용 (+ 자원 소모) ====
        auto_cfg = settings.get("auto_rest", {})
        auto_enabled = bool(auto_cfg.get("enabled", False))
        g_min = int(auto_cfg.get("min", 15))
        g_max = int(auto_cfg.get("max", 35))
        do_auto_fill = bool(settings.get("auto_recruit_from_pending", True))

        if auto_enabled:
            for uid, pdata in users.items():
                units = pdata.get("units", [])
                nation_name = pdata.get("nation") or "무소속"
                ensure_account(nation_name)
                # 현재 국가 자원 스냅샷
                try:
                    ndata = load_nation(nation_name) or {}
                    nres = (ndata.get("자원") or {}).copy()
                except Exception:
                    nres = {"식량": 10**9, "징집병": 10**9}

                total_gain = 0
                for u in units:
                    cur = int(u.get("인구", 0))
                    maxp = int(u.get("최대인구", cur or 0))
                    if maxp <= 0 or cur >= maxp:
                        continue
                    want = random.randint(g_min, g_max)
                    can_by_space = max(0, maxp - cur)
                    # 자원 한계: 징집병, 식량은 1:1로 소모
                    can_by_draft = int(nres.get("징집병", 0))
                    can_by_food = int(nres.get("식량", 0))
                    possible = min(want, can_by_space, can_by_draft, can_by_food)
                    if possible <= 0:
                        continue
                    # 실제 차감 시도(경합 대비 재확인)
                    if not spend(nation_name, {"징집병": possible, "식량": possible}):
                        retry = possible - 1
                        while retry > 0 and not spend(nation_name, {"징집병": retry, "식량": retry}):
                            retry -= 1
                        possible = retry
                        if possible <= 0:
                            continue
                    # 차감 반영(로컬 스냅샷도 줄여서 같은 턴 다른 유닛과 경합 방지)
                    nres["징집병"] = int(nres.get("징집병", 0)) - possible
                    nres["식량"] = int(nres.get("식량", 0)) - possible

                    before = cur
                    u["인구"] = min(maxp, cur + possible)
                    total_gain += (u["인구"] - before)

                # 해산 등으로 빈 슬롯 + pending 존재 시 자동 충원
                if do_auto_fill:
                    filled = self._auto_fill_from_pending(pdata)
                    if filled:
                        turn_summaries.append(f"➕ <@{uid}> → 대기 모집 `{filled}` 자동 충원")
                        self._append_user_event(user_events, uid, f"대기 모집 `{filled}` 자동 충원")

                if total_gain > 0:
                    turn_summaries.append(f"🌿 <@{uid}> → 자동회복으로 병력 +{total_gain} (국가 자원 소모 반영)")
                    self._append_user_event(user_events, uid, f"자동회복으로 병력 +{total_gain}")

        # ==== 3) 📦 예약 처리(각 1개) — 이동/건설만 ====
        moved_this_turn: Dict[str, Dict[str, str]] = {}  # uid -> {"from":..,"to":..}

        for uid, pdata in users.items():
            reservations = pdata.get("reservations", [])
            if not isinstance(reservations, list) or not reservations:
                continue

            act = reservations.pop(0)
            pdata["reservations"] = reservations  # pop 반영

            atype = act.get("type")
            if atype == "이동":
                cur = reverse_index.get(uid) or pdata.get("location")
                target = (act.get("target") or "").upper()
                if not (cur and target and self.is_valid_coord(target)):
                    turn_summaries.append(f"⚠️ <@{uid}> → 이동 실패(위치/좌표 오류)")
                    self._append_user_event(user_events, uid, f"이동 실패(위치/좌표 오류)")
                    continue
                elif not self.adjacent(cur, target):
                    turn_summaries.append(f"⚠️ <@{uid}> → 이동 실패(인접 아닌: {cur}→{target})")
                    self._append_user_event(user_events, uid, f"이동 실패(인접 아님: {cur}→{target})")
                    continue
                elif not self.passable(target):  # ✅ region 기준 체크
                    turn_summaries.append(f"⛔ <@{uid}> → 이동 실패(리건데이터에 없는 지역: {target})")
                    self._append_user_event(user_events, uid, f"이동 실패(지역 없음: {target})")
                    continue

                # 이동만 처리 (중립 교전은 공통 단계에서)
                if isinstance(region_data.get(cur), dict):
                    L = region_data[cur].setdefault("units", [])
                    if str(uid) in L:
                        L.remove(str(uid))
                if isinstance(region_data.get(target), dict):
                    L2 = region_data[target].setdefault("units", [])
                    if str(uid) not in L2:
                        L2.append(str(uid))
                reverse_index[uid] = target
                pdata["location"] = target
                moved_this_turn[uid] = {"from": cur, "to": target}

                turn_summaries.append(f"🚪 <@{uid}> → {cur} → **{target}** 이동")
                self._append_user_event(user_events, uid, f"{cur} → {target} 이동")

            elif atype == "건설":
                loc = act.get("location")
                building = act.get("building")
                turns_left = int(act.get("turns_left", 1))

                if not loc or loc not in region_data:
                    turn_summaries.append(f"⚠️ <@{uid}> → 건설 실패(지역 없음)")
                    self._append_user_event(user_events, uid, f"`{building or '?'}(건설)` 실패: 지역 없음")
                    continue
                if not building:
                    turn_summaries.append(f"⚠️ <@{uid}> → 건설 실패(건물 미지정)")
                    self._append_user_event(user_events, uid, f"건설 실패(건물 미지정)")
                    continue

                # --- 건설 비용 선납(첫 처리 시 한 번만) ---
                cost_paid = bool(act.get("cost_paid"))
                if not cost_paid:
                    nation_name = pdata.get("nation") or "무소속"
                    ensure_account(nation_name)
                    cost = building_cost(building)  # buildings.json의 "건설비용"
                    if cost:
                        if not spend(nation_name, cost):
                            turn_summaries.append(f"💸 <@{uid}> → `{loc}` {building} 건설 실패(자원 부족)")
                            self._append_user_event(user_events, uid, f"`{loc}` {building} 건설 실패(자원 부족)")
                            continue  # 이번 예약은 소모(비용 미납으로 진행 불가)
                    act["cost_paid"] = True

                # --- 공사 시간 진행 ---
                turns_left -= 1
                if turns_left > 0:
                    # 계속 건설 중 → 맨 앞에 재삽입해서 다음 턴에도 우선 처리
                    act["turns_left"] = turns_left
                    new_list = pdata.get("reservations", [])
                    pdata["reservations"] = [act] + new_list
                    turn_summaries.append(f"🏗️ <@{uid}> → `{loc}` {building} 건설 진행중 ({turns_left}턴 남음)")
                    self._append_user_event(user_events, uid, f"`{loc}` {building} 건설 진행중 ({turns_left}턴 남음)")
                else:
                    # 완공
                    slot = region_data.get(loc, {})
                    b = slot.setdefault("buildings", [])
                    if building not in b:
                        b.append(building)
                    turn_summaries.append(f"✅ <@{uid}> → `{loc}` {building} **건설 완료**")
                    self._append_user_event(user_events, uid, f"`{loc}` {building} 건설 완료")

            else:
                # 회복(type=='회복')은 제거됨 (자동회복으로 대체)
                turn_summaries.append(f"👤 <@{uid}> → `{act.get('action', atype or '행동')}`")
                self._append_user_event(user_events, uid, f"`{act.get('action', atype or '행동')}` 실행")

        # ==== 3.5) 🛡️ 중립(PvE) 교전 처리 — PvP 전에 실행 ====
        unit_defs = self.load_unit_defs(settings.get("season_number", 1))
        season_no = settings.get("season_number", 1)

        if isinstance(region_data, dict):
            for coord, slot in list(region_data.items()):
                if not isinstance(slot, dict):
                    continue
                neutral_list = slot.get("neutral_units") or []
                if not neutral_list:
                    continue  # 중립 수비 없음

                occupants = [uid for uid in (slot.get("units") or []) if uid in users]
                if not occupants:
                    continue  # 플레이어 없음

                # 플레이어 연합 vs 중립 수비
                side_players = self.build_side_from_players(occupants, unit_defs, users)
                side_neutral = self.build_side_from_neutral(neutral_list, unit_defs)
                if not side_players:
                    continue  # 플레이어 실제 병력 없음

                p_start = sum(u["체력"] for u in side_players)
                n_start = sum(u["체력"] for u in side_neutral)

                로그 = 전투처리(side_players, side_neutral, season_no)

                # 손실 반영
                self.apply_losses_to_users(side_players, users)
                self.apply_losses_to_neutral(slot, side_neutral)

                p_left = sum(u["체력"] for u in side_players)
                n_left = sum(u["체력"] for u in side_neutral)
                p_loss = max(0, p_start - p_left)
                n_loss = max(0, n_start - n_left)

                # 🔹 전체 요약(전투 목록)
                nations_here = list({users[uid].get("nation", "무소속") for uid in occupants})
                global_battles.append({"coord": coord, "kind": "PvE", "nations": nations_here})

                # 🔹 개인 전투 요약
                result = self._extract_battle_result(로그) or "전투 종료"
                self._append_user_battle(user_battles, occupants, f"[{coord}/PvE] 결과: {result}")

                # 🔔 공지용 라인 작성
                atk_uids = [u for u in occupants if (moved_this_turn.get(u, {}).get("to") == coord)]
                origin = None
                if atk_uids:
                    origins = {moved_this_turn[u]["from"] for u in atk_uids if "from" in moved_this_turn[u]}
                    origin = list(origins)[0] if len(origins) == 1 else "여러 지역"

                t_att = self._fmt_types(self._count_types(side_players))
                t_def = self._fmt_types(self._count_types(side_neutral))
                commanders_att = len(set(u["플레이어"] for u in side_players if u.get("플레이어") != "중립"))
                commanders_def = 1 if n_start > 0 else 0
                all_commanders = [u["플레이어"] for u in side_players if u["플레이어"] != "중립"]
                overall_cmd = random.choice(all_commanders) if all_commanders else "중립"

                winner_text = "플레이어 연합" if "공격측 승리" in result else "중립"
                announce_lines.append(
                    f"• {coord} 전투발생: {(origin + ' → ' + coord) if origin else coord}\n"
                    f"  - 유형: PvE | 지휘관: {commanders_att} vs {commanders_def} (총 {commanders_att+commanders_def})\n"
                    f"  - 병종 참가수: 공격측({t_att}) / 수비측({t_def})\n"
                    f"  - 총지휘관: {overall_cmd}\n"
                    f"  - 사상자: 공격측 {p_loss}명 / 수비측 {n_loss}명\n"
                    f"  - 결과: **{winner_text} 승리**"
                )

                # 중립 전멸 시 즉시 점령 시도
                captured = self.try_capture_slot(coord, slot, users)
                if captured:
                    turn_summaries.append(f"🏳️ `{coord}` → **{captured}** 점령")
                    for uid in occupants:
                        self._append_user_event(user_events, uid, f"`{coord}` 점령: {captured}")

        # 저장(예약/이동/건설/자동회복/충원/PvE 반영)
        for uid, pdata in users.items():
            try:
                with open(os.path.join(DATA_DIR, f"{uid}.json"), "w", encoding="utf-8") as f:
                    json.dump(pdata, f, indent=4, ensure_ascii=False)
            except Exception as e:
                print(f"⚠️ 저장 실패: {uid}.json -> {e}")
        try:
            with open(REGION_PATH, "w", encoding="utf-8") as f:
                json.dump(region_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ 지역 저장 실패: {REGION_PATH} -> {e}")

        # ==== 4) ⚔️ PvP 교전 감지 & 처리 ====
        if isinstance(region_data, dict):
            for coord, slot in region_data.items():
                if not isinstance(slot, dict):
                    continue
                occupants = slot.get("units", []) or []
                if len(occupants) < 2:
                    continue

                # 국가별 분류
                nations: Dict[str, List[str]] = {}
                for ouid in occupants:
                    pdata = users.get(ouid)
                    if not pdata or not isinstance(pdata, dict):
                        continue
                    nations.setdefault(pdata.get("nation", "무소속"), []).append(ouid)
                if len(nations) != 2:
                    if len(nations) >= 3:
                        battle_logs.append(f"⚖️ `{coord}`: 3개국 이상 집결 → 교전 보류")
                    continue

                na, nb = list(nations.keys())

                # 공격측 추정(해당 칸으로 "이번 턴" 이동한 다수 국적)
                moved_attackers = [u for u in occupants if (moved_this_turn.get(u, {}).get("to") == coord)]
                if moved_attackers:
                    counts = {}
                    for u in moved_attackers:
                        n = users[u].get("nation", "무소속")
                        counts[n] = counts.get(n, 0) + 1
                    attack_nation = max(counts.items(), key=lambda kv: kv[1])[0]
                else:
                    attack_nation = na  # 기본

                sideA_ids = nations.get(attack_nation, [])
                sideB_ids = [u for n, L in nations.items() if n != attack_nation for u in L]

                sideA_units = self.build_side_from_players(sideA_ids, unit_defs, users)
                sideB_units = self.build_side_from_players(sideB_ids, unit_defs, users)
                if not sideA_units or not sideB_units:
                    continue

                A_start = sum(u["체력"] for u in sideA_units)
                B_start = sum(u["체력"] for u in sideB_units)

                로그 = 전투처리(sideA_units, sideB_units, season_no)
                battle_logs.append(f"🗺️ `{coord}` 전투: **{na}** vs **{nb}**")
                battle_logs.extend(로그)

                # 손실 반영
                self.apply_losses_to_users(sideA_units + sideB_units, users)

                A_left = sum(u["체력"] for u in sideA_units)
                B_left = sum(u["체력"] for u in sideB_units)
                A_loss = max(0, A_start - A_left)
                B_loss = max(0, B_start - B_left)

                # 전멸한 유저 좌표에서 제거
                def alive(u_id: str):
                    return any(u.get("인구", 0) > 0 for u in users.get(u_id, {}).get("units", []))

                slot["units"] = [ouid for ouid in occupants if ouid in users and alive(ouid)]

                # 🔹 전체 요약(전투 목록)
                global_battles.append({"coord": coord, "kind": "PvP", "nations": [na, nb]})
                # 🔹 개인 전투 요약
                participants = sideA_ids + sideB_ids
                result = self._extract_battle_result(로그) or f"{na} vs {nb} 전투 종료"
                self._append_user_battle(user_battles, participants, f"[{coord}/PvP] 결과: {result}")

                # 🔔 공지용 라인 작성
                # 공격측/수비측 병종 수 & 지휘관 수
                t_att = self._fmt_types(self._count_types(sideA_units))
                t_def = self._fmt_types(self._count_types(sideB_units))
                commanders_att = len(set(users[uid].get("commander_name", uid) for uid in sideA_ids))
                commanders_def = len(set(users[uid].get("commander_name", uid) for uid in sideB_ids))
                all_commanders = list(set(users[uid].get("commander_name", uid) for uid in participants))
                overall_cmd = random.choice(all_commanders) if all_commanders else "미정"

                # 이동 공격 원점(가능 시)
                atk_uids = [u for u in participants if (moved_this_turn.get(u, {}).get("to") == coord)]
                origin = None
                if atk_uids:
                    origins = {moved_this_turn[u]["from"] for u in atk_uids if "from" in moved_this_turn[u]}
                    origin = list(origins)[0] if len(origins) == 1 else "여러 지역"

                # 승자 국가 이름
                if "공격측 승리" in result:
                    winner_name = attack_nation
                elif "수비측 승리" in result:
                    winner_name = [n for n in (na, nb) if n != attack_nation][0]
                else:
                    winner_name = "무승부"

                announce_lines.append(
                    f"• {coord} 전투발생: {(origin + ' → ' + coord) if origin else coord}\n"
                    f"  - 유형: PvP | 참여국: {na} vs {nb} | 지휘관: {commanders_att} vs {commanders_def} (총 {commanders_att+commanders_def})\n"
                    f"  - 병종 참가수: 공격측({t_att}) / 수비측({t_def})\n"
                    f"  - 총지휘관: {overall_cmd}\n"
                    f"  - 사상자: 공격측 {A_loss}명 / 수비측 {B_loss}명\n"
                    f"  - 결과: **{winner_name} 승리**"
                )

        # ==== 4.5) 🏳️ 최종 점령 스캔(무혈입성/전투 후 정리) ====
        if isinstance(region_data, dict):
            for coord, slot in list(region_data.items()):
                if not isinstance(slot, dict):
                    continue
                captured = self.try_capture_slot(coord, slot, users)
                if captured:
                    turn_summaries.append(f"🏳️ `{coord}` → **{captured}** 점령")
                    for uid in (slot.get("units") or []):
                        if uid in users:
                            self._append_user_event(user_events, uid, f"`{coord}` 점령: {captured}")

        # 전투 반영 저장 (PvP/최종 점령)
        for uid, pdata in users.items():
            try:
                with open(os.path.join(DATA_DIR, f"{uid}.json"), "w", encoding="utf-8") as f:
                    json.dump(pdata, f, indent=4, ensure_ascii=False)
            except Exception as e:
                print(f"⚠️ 저장 실패(전투반영): {uid}.json -> {e}")
        try:
            with open(REGION_PATH, "w", encoding="utf-8") as f:
                json.dump(region_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ 지역 저장 실패(전투반영): {e}")

        # ==== 5) 🗂️ 턴결과 파일 저장(메뉴 → 턴결과에서 사용) ====
        self._save_feeds(settings["current_turn"], global_battles, user_events, user_battles)

        # ==== 6) 🧭 맵 타일 자동 동기화 ====
        self._sync_map_tiles(region_data, settings)

        # ==== 7) 🔔 채널 공지 송출 ====
        await self._announce_turn(settings["current_turn"], announce_lines, settings)

    # -------------------- 사용자 명령어 --------------------
    @app_commands.command(name="예약", description="다음 턴에 수행할 행동을 예약합니다. (최대 4개)")
    @app_commands.describe(행동="예: 이동 A3, 건설 농장")
    async def 예약(self, interaction: discord.Interaction, 행동: str):
        user_id = str(interaction.user.id)
        file_path = os.path.join(DATA_DIR, f"{user_id}.json")

        os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(file_path):
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump({"reservations": []}, f)

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        reservations = data.get("reservations", [])
        if len(reservations) >= MAX_RESERVATIONS:
            await interaction.response.send_message(f"⚠️ 이미 {MAX_RESERVATIONS}개의 행동이 예약되어 있어요.", ephemeral=True)
            return

        reservations.append({"action": 행동})
        data["reservations"] = reservations

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

        await interaction.response.send_message(f"✅ `{행동}` 행동이 {len(reservations)}/{MAX_RESERVATIONS}로 예약되었어요!", ephemeral=True)

    @app_commands.command(name="예약확인", description="현재 예약된 행동 목록을 확인합니다.")
    async def 예약확인(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        file_path = os.path.join(DATA_DIR, f"{user_id}.json")

        if not os.path.exists(file_path):
            await interaction.response.send_message("❌ 아직 예약된 행동이 없어요. `/예약`으로 추가해보세요!", ephemeral=True)
            return

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        reservations = data.get("reservations", [])
        if not reservations:
            await interaction.response.send_message("📭 현재 예약된 행동이 없습니다.", ephemeral=True)
            return

        message = "📋 현재 예약 목록:\n"
        for i, r in enumerate(reservations, start=1):
            message += f"{i}. {r.get('action', r.get('type'))}\n"

        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="예약취소", description="특정 예약 행동을 취소합니다 (번호 입력)")
    @app_commands.describe(번호="취소할 예약 번호 (1부터 시작)")
    async def 예약취소(self, interaction: discord.Interaction, 번호: int):
        user_id = str(interaction.user.id)
        file_path = os.path.join(DATA_DIR, f"{user_id}.json")

        if not os.path.exists(file_path):
            await interaction.response.send_message("❌ 아직 예약된 행동이 없어요.", ephemeral=True)
            return

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        reservations = data.get("reservations", [])
        if 번호 < 1 or 번호 > len(reservations):
            await interaction.response.send_message("⚠️ 유효하지 않은 번호입니다.", ephemeral=True)
            return

        취소한_행동 = reservations.pop(번호 - 1)
        data["reservations"] = reservations

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

        await interaction.response.send_message(f"🗑️ `{취소한_행동.get('action', 취소한_행동.get('type'))}` 예약이 취소되었습니다.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Reserve(bot))
