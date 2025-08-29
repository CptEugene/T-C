# services/scheduler.py — 이름(지역명) 기반 턴 스케줄러 완성본
# - 기존 commands/reserve.py 의 코어(턴 루프/예약/전투/건설/회복/피드)를 서비스로 분리
# - 좌표(A1,C3) 제거, 지역명(예: "남항구") 기반으로 이동/전투 처리
# - 슬래시 명령어는 여기 없습니다. UI(버튼/드롭다운)에서 queue_action()만 호출하세요.

from __future__ import annotations
import os, json, asyncio, random, re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

# --- 외부 서비스 의존 ---
try:
    from services.move import is_valid_region, is_connected, passable
except Exception:
    # 최소 안전장치 (개발 중 비상)
    def is_valid_region(name: str) -> bool: return True
    def is_connected(a: str, b: str) -> bool: return True
    def passable(name: str) -> bool: return True

try:
    from services.combat import 전투처리
except Exception:
    def 전투처리(a, b, season):
        return ["[stub] 전투 처리: 시즌 %s" % season, "결과: 무승부"]

try:
    from services.economy import tick_production, building_cost, spend, ensure_account, can_spend, load_nation
except Exception:
    def tick_production(_region): return {}
    def building_cost(_name): return {}
    def spend(_nation, _cost): return True
    def ensure_account(_nation): return None
    def can_spend(_nation, _cost): return True
    def load_nation(_nation): return {"자원": {"돈": 10**9, "식량": 10**9, "연료": 10**9, "징집병": 10**9}}

# --- 상수/경로 ---
KST = timezone(timedelta(hours=9))
DATA_DIR = "data"
REGION_PATH = os.path.join(DATA_DIR, "region_data.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "game_settings.json")
TURN_FEED_FILE = os.path.join(DATA_DIR, "turn_feed.json")
USER_FEED_DIR = os.path.join(DATA_DIR, "user_feed")
MAX_RESERVATIONS = 4

# --- 내부 상태 (공지 채널 송출용) ---
_bot = None  # start_background_tasks(bot)에서 주입

# =========================
# 유틸: 파일 I/O
# =========================

def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path: str, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# 유저 파일 로드/저장

def load_user(uid: str) -> dict:
    return _load_json(os.path.join(DATA_DIR, f"{uid}.json"), {})

def save_user(uid: str, data: dict):
    _save_json(os.path.join(DATA_DIR, f"{uid}.json"), data)

# 시스템 설정

def load_settings() -> dict:
    return _load_json(SETTINGS_FILE, {
        "season_number": 1,
        "auto_rest": {"enabled": True, "min": 15, "max": 35},
        "auto_recruit_from_pending": True,
        # 공지 채널: "announce_channel": 1234567890
    })

def save_settings(s: dict):
    _save_json(SETTINGS_FILE, s)

# 지역 데이터

def load_region() -> dict:
    return _load_json(REGION_PATH, {})

def save_region(d: dict):
    _save_json(REGION_PATH, d)

# 유닛 정의(시즌별)

def _load_unit_defs(season_number: int) -> dict:
    path = os.path.join(DATA_DIR, f"units_season{season_number}.json")
    return _load_json(path, {})

# =========================
# 예약/이동/건설 API (UI에서 호출)
# =========================

def queue_action(uid: str, action: dict) -> bool:
    """UI에서 예약 push: {'type':'이동','target':'남항구'} 등. 최대 4개."""
    data = load_user(uid)
    res = data.setdefault("reservations", [])
    if len(res) >= MAX_RESERVATIONS:
        return False
    res.append(action)
    save_user(uid, data)
    return True


# =========================
# 턴 루프 제어
# =========================

async def _loop():
    ensure_region_schema()
    while True:
        now = datetime.now(KST)
        minute_blocks = [0, 15, 30, 45]  # 15분 턴
        next_minute = next((m for m in minute_blocks if now.minute < m), 60)
        next_turn_time = now.replace(minute=next_minute % 60, second=0, microsecond=0)
        if next_minute == 60:
            next_turn_time += timedelta(hours=1)
        await asyncio.sleep(max(1.0, (next_turn_time - now).total_seconds()))
        await process_turn()

def start_background_tasks(bot):
    """봇 로그인 후 on_ready에서 한 번 호출하세요."""
    global _bot
    _bot = bot
    bot.loop.create_task(_loop())

# =========================
# 코어: 한 턴 처리
# =========================

def _auto_fill_from_pending(user: dict) -> Optional[str]:
    """슬롯 여유 + pending_recruitment 있으면 자동 충원 (100명 투입, 최대인구 제한).
    returns: 충원된 유닛 이름 or None"""
    recruit = user.get("pending_recruitment")
    if not recruit:
        return None
    units = user.setdefault("units", [])
    # 간단 슬롯 규칙: 기본 4 + 전략 수치(없으면 0)
    slot_limit = 4 + int(user.get("stats", {}).get("전략", 0))
    if len(units) >= slot_limit:
        return None
    max_pop = int(recruit.get("최대인구", 500) or 500)
    units.append({
        "이름": recruit["병종"],
        "병종": recruit["병종"],
        "인구": min(100, max_pop),
        "최대인구": max_pop,
    })
    user.pop("pending_recruitment", None)
    return recruit["병종"]

def _to_attack_type(unit_name: str, unit_defs: dict) -> str:
    info = unit_defs.get(unit_name, {})
    troop_class = info.get("병종유형") or info.get("병종")
    mapping = {
        "검병": "돌격", "창병": "돌격", "방패병": "전열", "장창병": "전열",
        "궁병": "사격", "화승총병": "사격", "석궁병": "사격",
        "기병": "기동", "경기병": "기동", "중기병": "돌격",
        "전투기": "기동", "뇌격기": "기동", "함선": "전열",
    }
    if troop_class in mapping:
        return mapping[troop_class]
    if "전투기" in unit_name or "뇌격기" in unit_name:
        return "기동"
    return "전열"

def _build_side_from_players(player_ids: List[str], unit_defs: Dict[str, dict], user_datas: Dict[str, dict]) -> List[dict]:
    side = []
    for uid in player_ids:
        pdata = user_datas.get(uid) or {}
        commander = pdata.get("commander_name", uid)
        for u in (pdata.get("units") or []):
            name = u.get("이름")
            pop = int(u.get("인구", 0))
            if not name or pop <= 0:
                continue
            base = unit_defs.get(name, {})
            side.append({
                "이름": name,
                "플레이어": commander,
                "공격력": int(base.get("공격력", 10)),
                "방어력": int(base.get("방어력", 0)),
                "공격유형": _to_attack_type(name, unit_defs),
                "체력": pop,
                "표기방식": base.get("표기방식", "병력"),
                "_owner_id": uid,
            })
    return side

def _apply_losses_to_users(combat_list: List[dict], user_datas: Dict[str, dict]):
    from collections import defaultdict
    left_sum = defaultdict(int)
    for cu in combat_list:
        left_sum[(cu.get("_owner_id"), cu.get("이름"))] += max(0, int(cu.get("체력", 0)))
    for uid, pdata in user_datas.items():
        name_left = {name: left for (u, name), left in left_sum.items() if u == uid}
        new_units = []
        for u in (pdata.get("units") or []):
            name = u.get("이름")
            cur_left = int(name_left.get(name, 0))
            if cur_left <= 0:
                continue
            keep = min(int(u.get("인구", 0)), cur_left)
            u["인구"] = keep
            name_left[name] = max(0, cur_left - keep)
            if u["인구"] > 0:
                new_units.append(u)
        pdata["units"] = new_units

# PvE 중립 변환/손실 반영

def _build_side_from_neutral(neutral_list: List[dict], unit_defs: dict) -> List[dict]:
    side = []
    for nu in (neutral_list or []):
        name = nu.get("이름")
        hp = int(nu.get("인구", 0))
        if not name or hp <= 0:
            continue
        base = unit_defs.get(name, {})
        side.append({
            "이름": name,
            "플레이어": "중립",
            "공격력": int(base.get("공격력", 10)),
            "방어력": int(base.get("방어력", 10)),
            "공격유형": _to_attack_type(name, unit_defs),
            "체력": hp,
            "표기방식": base.get("표기방식", "병력"),
            "_neutral": True,
        })
    return side

def _apply_losses_to_neutral(slot: dict, neutral_combat_list: List[dict]):
    left, total = [], 0
    for cu in neutral_combat_list:
        name = cu.get("이름")
        hp = max(0, int(cu.get("체력", 0)))
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

# 점령 판정

def _try_capture_slot(region_name: str, slot: dict, users: Dict[str, dict]) -> Optional[str]:
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

# 피드 유틸

def _init_feeds(users: Dict[str, dict]):
    user_events = {uid: [] for uid in users.keys()}
    user_battles = {uid: [] for uid in users.keys()}
    global_battles: List[dict] = []
    return user_events, user_battles, global_battles

def _append_user_event(user_events: Dict[str, List[str]], uid: str, text: str):
    user_events.setdefault(uid, []).append(text)

def _append_user_battle(user_battles: Dict[str, List[str]], participants: List[str], text: str):
    for uid in participants:
        user_battles.setdefault(uid, []).append(text)

def _extract_battle_result(logs: List[str]) -> Optional[str]:
    for line in reversed(logs or []):
        m = re.search(r"결과:\s*([^\n]+)", line)
        if m:
            return m.group(1).strip()
    return None

def _save_feeds(turn: int, global_battles: List[dict], user_events: Dict[str, List[str]], user_battles: Dict[str, List[str]]):
    try:
        _save_json(TURN_FEED_FILE, {"turn": turn, "battles": global_battles[:200]})
    except Exception as e:
        print(f"⚠️ turn_feed 저장 실패: {e}")
    try:
        os.makedirs(USER_FEED_DIR, exist_ok=True)
        for uid in set(user_events.keys()) | set(user_battles.keys()):
            payload = {"turn": turn, "events": user_events.get(uid, [])[:100], "battles": user_battles.get(uid, [])[:100]}
            _save_json(os.path.join(USER_FEED_DIR, f"{uid}.json"), payload)
    except Exception as e:
        print(f"⚠️ user_feed 저장 실패: {e}")

# 공지 송출
async def _announce_turn(turn: int, lines: List[str], settings: dict):
    ch_id = settings.get("announce_channel")
    if not ch_id or not _bot:
        return
    channel = _bot.get_channel(int(ch_id))
    if not channel:
        return
    header = f"⏱️ **턴 {turn}** 결과"
    text = "\n".join([header] + (lines or ["세상이 평화롭습니다."]))
    # 2000자 분할
    chunks = []
    while len(text) > 1900:
        cut = text.rfind("\n", 0, 1900)
        if cut == -1: cut = 1900
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    for c in chunks:
        try:
            await channel.send(c)
        except Exception as e:
            print(f"[scheduler] 공지 송출 실패: {e}")
            break

# 스키마 보정 (옛 unit -> units)

def ensure_region_schema():
    data = load_region()
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
        save_region(data)

# =========================
# 이동/건설/회복/전투: 한 턴 처리
# =========================

async def process_turn():
    settings = load_settings()
    turn = int(settings.get("current_turn", 0)) + 1
    settings["current_turn"] = turn
    save_settings(settings)

    turn_summaries: List[str] = []
    announce_lines: List[str] = []

    # 유저 로드
    users: Dict[str, dict] = {}
    for filename in os.listdir(DATA_DIR):
        if not filename.endswith('.json'): 
            continue
        name = filename[:-5]
        if not name.isdigit():
            continue
        pdata = load_user(name)
        if isinstance(pdata, dict):
            users[name] = pdata

    user_events, user_battles, global_battles = _init_feeds(users)

    # 지역 로드
    region = load_region()

    # 생산 처리
    prod = tick_production(region)  # {nation: {자원명: 증가량}}
    for n, delta in (prod or {}).items():
        pretty = ", ".join(f"{k}+{v}" for k, v in delta.items())
        turn_summaries.append(f"🏭 **{n}** 생산: {pretty}")

    # 자동 회복 + 자동 충원
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
                can_by_draft = int(nres.get("징집병", 0))
                can_by_food = int(nres.get("식량", 0))
                possible = min(want, can_by_space, can_by_draft, can_by_food)
                if possible <= 0:
                    continue
                if not spend(nation_name, {"징집병": possible, "식량": possible}):
                    retry = possible - 1
                    while retry > 0 and not spend(nation_name, {"징집병": retry, "식량": retry}):
                        retry -= 1
                    possible = retry
                    if possible <= 0:
                        continue
                nres["징집병"] = int(nres.get("징집병", 0)) - possible
                nres["식량"] = int(nres.get("식량", 0)) - possible
                before = cur
                u["인구"] = min(maxp, cur + possible)
                total_gain += (u["인구"] - before)
            if do_auto_fill:
                filled = _auto_fill_from_pending(pdata)
                if filled:
                    turn_summaries.append(f"➕ <@{uid}> → 대기 모집 `{filled}` 자동 충원")
                    _append_user_event(user_events, uid, f"대기 모집 `{filled}` 자동 충원")
            if total_gain > 0:
                turn_summaries.append(f"🌿 <@{uid}> → 자동회복으로 병력 +{total_gain}")
                _append_user_event(user_events, uid, f"자동회복으로 병력 +{total_gain}")

    # 예약 처리 (한 턴당 1개만 소비)
    moved_this_turn: Dict[str, Dict[str, str]] = {}

    # 역인덱스(현재 점유 지역) 구성
    reverse_index: Dict[str, str] = {}
    if isinstance(region, dict):
        for rname, slot in region.items():
            if not isinstance(slot, dict):
                continue
            for uid in slot.get("units", []) or []:
                reverse_index[str(uid)] = rname

    for uid, pdata in users.items():
        actions = pdata.get("reservations", [])
        if not actions:
            continue
        act = actions.pop(0)
        pdata["reservations"] = actions

        atype = act.get("type") or act.get("action")
        if atype == "이동":
            cur = reverse_index.get(uid) or pdata.get("location")
            target = act.get("target")
            if not (cur and target and is_valid_region(target)):
                turn_summaries.append(f"⚠️ <@{uid}> → 이동 실패(위치/지역 오류)")
                _append_user_event(user_events, uid, f"이동 실패(위치/지역 오류)")
            elif not is_connected(cur, target):
                turn_summaries.append(f"⚠️ <@{uid}> → 이동 실패(비연결: {cur}→{target})")
                _append_user_event(user_events, uid, f"이동 실패(비연결: {cur}→{target})")
            elif not passable(target):
                turn_summaries.append(f"⛔ <@{uid}> → 이동 실패(통행 금지: {target})")
                _append_user_event(user_events, uid, f"이동 실패(통행 금지: {target})")
            else:
                # 지역 점유 목록 갱신
                if isinstance(region.get(cur), dict):
                    lst = region[cur].setdefault("units", [])
                    if uid in lst: lst.remove(uid)
                region.setdefault(target, {}).setdefault("units", [])
                if uid not in region[target]["units"]:
                    region[target]["units"].append(uid)
                reverse_index[uid] = target
                pdata["location"] = target
                moved_this_turn[uid] = {"from": cur, "to": target}
                turn_summaries.append(f"🚪 <@{uid}> → {cur} → **{target}** 이동")
                _append_user_event(user_events, uid, f"{cur} → {target} 이동")
        elif atype == "건설":
            loc = act.get("location")
            building = act.get("building")
            turns_left = int(act.get("turns_left", 1))
            if not loc or loc not in region:
                turn_summaries.append(f"⚠️ <@{uid}> → 건설 실패(지역 없음)")
                _append_user_event(user_events, uid, f"건설 실패(지역 없음)")
            elif not building:
                turn_summaries.append(f"⚠️ <@{uid}> → 건설 실패(건물 미지정)")
                _append_user_event(user_events, uid, f"건설 실패(건물 미지정)")
            else:
                cost_paid = bool(act.get("cost_paid"))
                if not cost_paid:
                    nation_name = pdata.get("nation") or "무소속"
                    ensure_account(nation_name)
                    cost = building_cost(building)
                    if cost and not spend(nation_name, cost):
                        turn_summaries.append(f"💸 <@{uid}> → `{loc}` {building} 건설 실패(자원 부족)")
                        _append_user_event(user_events, uid, f"`{loc}` {building} 건설 실패(자원 부족)")
                        # 비용 미납 시 이 예약은 소모됨
                    else:
                        act["cost_paid"] = True
                        turns_left -= 1
                else:
                    turns_left -= 1

                if act.get("cost_paid"):
                    if turns_left > 0:
                        act["turns_left"] = turns_left
                        pdata["reservations"] = [act] + pdata.get("reservations", [])
                        _append_user_event(user_events, uid, f"`{loc}` {building} 건설 진행중 ({turns_left}턴 남음)")
                        turn_summaries.append(f"🏗️ <@{uid}> → `{loc}` {building} 건설 진행중 ({turns_left}턴 남음)")
                    else:
                        slot = region.setdefault(loc, {})
                        b = slot.setdefault("buildings", [])
                        if building not in b:
                            b.append(building)
                        _append_user_event(user_events, uid, f"`{loc}` {building} 건설 완료")
                        turn_summaries.append(f"✅ <@{uid}> → `{loc}` {building} **건설 완료**")
        
        elif atype == "훈련":
            # 유저 사기(또는 부대 사기) 상승 — 간단히 유저 단위 morale로 관리
            morale = int(pdata.get("morale", 50))
            gain = 10  # 필요시 설정화 가능
            pdata["morale"] = min(100, morale + gain)
            _append_user_event(user_events, uid, f"훈련 실시: 사기 +{gain} (현재 {pdata['morale']})")
            turn_summaries.append(f"📗 <@{uid}> → 훈련으로 사기 +{gain}")

        # 저장(개별 유저)
        save_user(uid, pdata)

    # 지역 저장 (이동 반영)
    save_region(region)

    # === PvE (중립) 교전 ===
    unit_defs = _load_unit_defs(int(settings.get("season_number", 1)))
    season_no = int(settings.get("season_number", 1))

    if isinstance(region, dict):
        for rname, slot in list(region.items()):
            if not isinstance(slot, dict):
                continue
            neutral_list = slot.get("neutral_units") or []
            if not neutral_list:
                continue
            occupants = [uid for uid in (slot.get("units") or []) if uid in users]
            if not occupants:
                continue
            side_players = _build_side_from_players(occupants, unit_defs, users)
            side_neutral = _build_side_from_neutral(neutral_list, unit_defs)
            if not side_players:
                continue
            p_start = sum(u["체력"] for u in side_players)
            n_start = sum(u["체력"] for u in side_neutral)
            로그 = 전투처리(side_players, side_neutral, season_no)
            _apply_losses_to_users(side_players, users)
            _apply_losses_to_neutral(slot, side_neutral)
            p_left = sum(u["체력"] for u in side_players)
            n_left = sum(u["체력"] for u in side_neutral)
            p_loss = max(0, p_start - p_left)
            n_loss = max(0, n_start - n_left)
            # 개인/전체 피드
            global_battles.append({"region": rname, "kind": "PvE"})
            result = _extract_battle_result(로그) or "전투 종료"
            _append_user_battle(user_battles, occupants, f"[{rname}/PvE] 결과: {result}")
            # 공지 라인
            # 공격 원점 추정
            atk_uids = [u for u in occupants if (moved_this_turn.get(u, {}).get("to") == rname)]
            origin = None
            if atk_uids:
                origins = {moved_this_turn[u]["from"] for u in atk_uids if "from" in moved_this_turn[u]}
                origin = list(origins)[0] if len(origins) == 1 else "여러 지역"
            winner_text = "플레이어 연합" if "공격측 승리" in (result or "") else "중립"
            announce_lines.append(
                f"• {rname} 전투발생: {(origin + ' → ' + rname) if origin else rname}\n"
                f"  - 유형: PvE\n"
                f"  - 사상자: 공격측 {p_loss}명 / 수비측 {n_loss}명\n"
                f"  - 결과: **{winner_text} 승리**"
            )
            # 점령 시도
            captured = _try_capture_slot(rname, slot, users)
            if captured:
                turn_summaries.append(f"🏳️ `{rname}` → **{captured}** 점령")
                for uid in occupants:
                    _append_user_event(user_events, uid, f"`{rname}` 점령: {captured}")

    # 저장 (PvE 반영)
    for uid, pdata in users.items():
        save_user(uid, pdata)
    save_region(region)

    # === PvP ===
    if isinstance(region, dict):
        for rname, slot in region.items():
            if not isinstance(slot, dict):
                continue
            occupants = slot.get("units", []) or []
            if len(occupants) < 2:
                continue
            # 국가별 묶기
            nations: Dict[str, List[str]] = {}
            for ouid in occupants:
                pdata = users.get(ouid)
                if not pdata:
                    continue
                nations.setdefault(pdata.get("nation", "무소속"), []).append(ouid)
            if len(nations) != 2:
                continue  # 2국 교전만 처리
            na, nb = list(nations.keys())
            moved_attackers = [u for u in occupants if (moved_this_turn.get(u, {}).get("to") == rname)]
            if moved_attackers:
                counts: Dict[str, int] = {}
                for u in moved_attackers:
                    n = users[u].get("nation", "무소속")
                    counts[n] = counts.get(n, 0) + 1
                attack_nation = max(counts.items(), key=lambda kv: kv[1])[0]
            else:
                attack_nation = na
            sideA_ids = nations.get(attack_nation, [])
            sideB_ids = [u for n, L in nations.items() if n != attack_nation for u in L]
            sideA_units = _build_side_from_players(sideA_ids, unit_defs, users)
            sideB_units = _build_side_from_players(sideB_ids, unit_defs, users)
            if not sideA_units or not sideB_units:
                continue
            A_start = sum(u["체력"] for u in sideA_units)
            B_start = sum(u["체력"] for u in sideB_units)
            로그 = 전투처리(sideA_units, sideB_units, season_no)
            _apply_losses_to_users(sideA_units + sideB_units, users)
            A_left = sum(u["체력"] for u in sideA_units)
            B_left = sum(u["체력"] for u in sideB_units)
            A_loss = max(0, A_start - A_left)
            B_loss = max(0, B_start - B_left)
            # 전멸 유저 제거
            def _alive(u_id: str) -> bool:
                return any(u.get("인구", 0) > 0 for u in users.get(u_id, {}).get("units", []))
            slot["units"] = [ouid for ouid in occupants if ouid in users and _alive(ouid)]
            # 피드
            global_battles.append({"region": rname, "kind": "PvP", "nations": [na, nb]})
            participants = sideA_ids + sideB_ids
            result = _extract_battle_result(로그) or f"{na} vs {nb} 전투 종료"
            _append_user_battle(user_battles, participants, f"[{rname}/PvP] 결과: {result}")
            # 공지 라인
            if "공격측 승리" in result:
                winner = attack_nation
            elif "수비측 승리" in result:
                winner = [n for n in (na, nb) if n != attack_nation][0]
            else:
                winner = "무승부"
            announce_lines.append(
                f"• {rname} 전투발생 (PvP) — {na} vs {nb}\n"
                f"  - 사상자: 공격측 {A_loss} / 수비측 {B_loss}\n"
                f"  - 결과: **{winner} 승리**"
            )

    # 최종 점령 스캔
    if isinstance(region, dict):
        for rname, slot in list(region.items()):
            captured = _try_capture_slot(rname, slot, users)
            if captured:
                turn_summaries.append(f"🏳️ `{rname}` → **{captured}** 점령")
                for uid in (slot.get("units") or []):
                    if uid in users:
                        _append_user_event(user_events, uid, f"`{rname}` 점령: {captured}")

    # 저장
    for uid, pdata in users.items():
        save_user(uid, pdata)
    save_region(region)

    # 피드 저장 & 공지
    _save_feeds(turn, global_battles, user_events, user_battles)
    # 이미지 지도 체제로 전환했으므로 타일 동기화(map.json)는 생략
    await _announce_turn(turn, announce_lines, settings)
