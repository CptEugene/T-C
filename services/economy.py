# commands/economy.py
import os, json
from typing import Dict, Optional, Tuple

DATA_DIR = "data"
BUILDINGS_FILE = os.path.join(DATA_DIR, "buildings.json")

# 국가 계정 기본값(처음 생성 시)
DEFAULT_RESOURCES = {"돈": 0, "식량": 0, "연료": 0, "징집병": 0}
# 기본 저장 한도(저장소 없이도 약간은 보유 가능하도록)
DEFAULT_BASE_CAP = {"돈": 1000, "식량": 1000, "연료": 1000, "징집병": 1000}

# ===== 공용 유틸 =====
def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)

def _nation_file(nation_name: str) -> str:
    _ensure_dir()
    return os.path.join(DATA_DIR, f"{nation_name}.json")

def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: str, data):
    _ensure_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# ===== 국가 계정 =====
def load_nation(nation_name: str) -> Dict:
    data = load_json(_nation_file(nation_name), {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("국가", nation_name)
    data.setdefault("자원", DEFAULT_RESOURCES.copy())
    data.setdefault("자원한도", DEFAULT_BASE_CAP.copy())  # 동적 재계산도 가능하지만, 파일에도 유지
    return data

def save_nation(nation_name: str, data: Dict):
    save_json(_nation_file(nation_name), data)

def ensure_account(nation_name: str):
    save_nation(nation_name, load_nation(nation_name))

def can_spend(nation_name: str, cost: Dict) -> bool:
    inv = load_nation(nation_name).setdefault("자원", {})
    for k, v in (cost or {}).items():
        if int(inv.get(k, 0)) < int(v):
            return False
    return True

def spend(nation_name: str, cost: Dict) -> bool:
    data = load_nation(nation_name)
    inv = data.setdefault("자원", {})
    # 검증
    for k, v in (cost or {}).items():
        if int(inv.get(k, 0)) < int(v):
            return False
    # 차감
    for k, v in (cost or {}).items():
        inv[k] = int(inv.get(k, 0)) - int(v)
    save_nation(nation_name, data)
    return True

def add_with_caps(nation_name: str, delta: Dict, caps: Optional[Dict]=None):
    """자원에 delta를 더하되, caps(자원한도)를 초과하지 않도록 보정."""
    data = load_nation(nation_name)
    inv = data.setdefault("자원", {})
    cur_caps = (caps or data.get("자원한도") or DEFAULT_BASE_CAP).copy()
    for k, v in (delta or {}).items():
        inv[k] = int(inv.get(k, 0)) + int(v)
        cap = int(cur_caps.get(k, 10**9))  # cap 정의가 없으면 사실상 무제한
        if inv[k] > cap:
            inv[k] = cap
    # 최신 한도도 저장(외부에서 계산해 온 값을 우선 저장)
    if caps:
        data["자원한도"] = {k: int(v) for k, v in cur_caps.items()}
    save_nation(nation_name, data)

# ===== 빌딩 정의 읽기 =====
def load_buildings() -> Dict[str, dict]:
    return load_json(BUILDINGS_FILE, {}) or {}

def building_cost(name: str) -> Dict[str, int]:
    """buildings.json의 '건설비용'을 그대로 사용. (예: {'돈': 1500})"""
    bdefs = load_buildings()
    info = (bdefs.get(name) or {})
    raw = info.get("건설비용") or {}
    if not isinstance(raw, dict):
        return {}
    return {k: int(v) for k, v in raw.items()}

# ===== 생산/한도 계산 =====
def _effect_to_yield(effect: Dict) -> Dict[str, int]:
    """효과 키를 리소스 증감으로 변환"""
    out = {}
    if not isinstance(effect, dict):
        return out
    # 생산
    if "징집병생산" in effect:
        out["징집병"] = out.get("징집병", 0) + int(effect["징집병생산"])
    if "식량생산" in effect:
        out["식량"] = out.get("식량", 0) + int(effect["식량생산"])
    if "연료생산" in effect:
        out["연료"] = out.get("연료", 0) + int(effect["연료생산"])
    if "수익" in effect:
        out["돈"] = out.get("돈", 0) + int(effect["수익"])
    # 방어력보너스/해상생산허용 등은 경제 산출과 무관 → 무시
    return out

def _effect_to_cap_increase(effect: Dict) -> int:
    """저장소 효과: 자원한도증가 합산값 반환(없으면 0)"""
    if not isinstance(effect, dict):
        return 0
    return int(effect.get("자원한도증가", 0))

def compute_caps_for_nation(region_data: Dict, nation_id: str, base_cap: Dict[str, int]) -> Dict[str, int]:
    """
    nation_id(RED/BLUE)에 속한 모든 지역 건물 중 '저장소'의 자원한도증가를 합산하여
    자원별 한도를 계산. (한도는 모든 자원에 동일 수치로 적용)
    """
    bdefs = load_buildings()
    inc_total = 0
    for slot in (region_data or {}).values():
        if not isinstance(slot, dict):
            continue
        if slot.get("owner") != nation_id:
            continue
        for b in slot.get("buildings", []) or []:
            info = bdefs.get(b) or {}
            inc_total += _effect_to_cap_increase(info.get("효과") or {})
    caps = {}
    for k, v in base_cap.items():
        caps[k] = int(v) + int(inc_total)
    return caps

def tick_production(region_data: Dict) -> Dict[str, Dict[str, int]]:
    """
    지역의 건물 효과로 국가별 생산치를 합산하여 국가 계정에 반영.
    return: {nation_name: {자원: +값, ...}}
    """
    from .nations import NATION_DATA  # id->name 매핑 사용
    bdefs = load_buildings()
    totals_by_name: Dict[str, Dict[str, int]] = {}
    caps_by_name: Dict[str, Dict[str, int]] = {}

    # 먼저 국가별 한도 계산
    nation_ids = set()
    for slot in (region_data or {}).values():
        if isinstance(slot, dict) and slot.get("owner") in ("RED", "BLUE"):
            nation_ids.add(slot["owner"])
    # nation_id -> nation_name
    id2name = {nid: NATION_DATA[nid]["name"] for nid in nation_ids if nid in NATION_DATA}
    for nid, nname in id2name.items():
        caps_by_name[nname] = compute_caps_for_nation(region_data, nid, DEFAULT_BASE_CAP)

    # 생산 합산
    for slot in (region_data or {}).values():
        if not isinstance(slot, dict):
            continue
        nid = slot.get("owner")
        if nid not in ("RED", "BLUE"):
            continue
        nname = id2name.get(nid)
        if not nname:
            continue

        for b in slot.get("buildings", []) or []:
            info = bdefs.get(b) or {}
            eff = info.get("효과") or {}
            delta = _effect_to_yield(eff)
            if not delta:
                continue
            bucket = totals_by_name.setdefault(nname, {})
            for rk, rv in delta.items():
                bucket[rk] = int(bucket.get(rk, 0)) + int(rv)

    # 국가 계정에 반영(한도 적용)
    for nname, delta in totals_by_name.items():
        add_with_caps(nname, delta, caps_by_name.get(nname))

    return totals_by_name
