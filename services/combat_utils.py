# combat_utils.py
import os, json

DATA_DIR = "data"

# 병종 → 공격유형(전열/기동/사격/돌격) 간단 매핑
ATTACK_TYPE_BY_CLASS = {
    "검병": "돌격", "창병": "돌격", "방패병": "전열", "장창병": "전열",
    "궁병": "사격", "화승총병": "사격", "석궁병": "사격",
    "기병": "기동", "경기병": "기동", "중기병": "돌격",
    # 해상/항공 키워드
    "전투기": "기동", "뇌격기": "기동", "함선": "전열"
}

def load_settings():
    path = os.path.join(DATA_DIR, "game_settings.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"season_number": 1}

def load_unit_defs(season_number: int):
    path = os.path.join(DATA_DIR, f"units_season{season_number}.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def to_attack_type(unit_name: str, unit_defs: dict):
    info = unit_defs.get(unit_name, {})
    cls = info.get("병종")
    if cls in ATTACK_TYPE_BY_CLASS:
        return ATTACK_TYPE_BY_CLASS[cls]
    # 이름에 전투기/뇌격기/함선 포함 시 보정
    if "전투기" in unit_name or "뇌격기" in unit_name:
        return "기동"
    return "전열"

def build_side_from_players(player_ids, unit_defs, user_datas):
    """
    player_ids: 리스트[str]
    unit_defs: 시즌 유닛 정의 dict (이름->정의)
    user_datas: user_id(str) -> 로드된 유저 json
    return: 전투유닛리스트(List[Dict])
    """
    combat_units = []
    for uid in player_ids:
        pdata = user_datas[uid]
        commander = pdata.get("commander_name", uid)
        for u in pdata.get("units", []):
            name = u["이름"]
            base = unit_defs.get(name, {})
            atk = base.get("공격력", 10)
            # 방어력은 combat.py에서 직접 쓰진 않지만 남겨둠
            # 최대인구가 없을 수도 있어 안전 처리
            pop = max(0, int(u.get("인구", 0)))
            if pop <= 0:
                continue
            combat_units.append({
                "이름": name,
                "플레이어": commander,
                "공격력": atk,
                "방어력": base.get("방어력", 0),
                "공격유형": to_attack_type(name, unit_defs),
                "체력": pop,                 # 체력=현재 인구
                "표기방식": base.get("표기방식", "병력"),
                "_owner_id": uid,            # 전투 후 반영 위해 보관
                "_index": None               # 나중에 매칭할 필요 있으면 사용
            })
    return combat_units

def apply_losses_to_users(combat_list, user_datas):
    """
    combat_list의 체력 감소분을 각 유저 데이터(units[].인구)에 반영
    """
    # 유닛별 손실 = (전투 시작 전 인구) - (체력(=남은 인구))
    # 시작 전 인구는 combat_list 내에만 있으니, 반영 시 현재 인구와 비교해 최소값 사용
    from collections import defaultdict
    losses_by_user_and_name = defaultdict(int)

    for cu in combat_list:
        uid = cu["_owner_id"]
        # 남은 체력=남은 인구로 본다. 시작 전 인구는 알 수 없으니 현재 유저 데이터와 차이로 처리 X
        # 대신, 전투 종료 시점 combat_list의 체력이 "남은 인구"이므로 그 값으로 세팅.
        losses_by_user_and_name[(uid, cu["이름"])] += cu["체력"]  # 일단 남은 인구 합산

    # 이름이 같은 유닛이 여러 개일 수도 있으니, 순서대로 덮지 말고 분배 로직
    for uid, pdata in user_datas.items():
        # 유저가 들고 있는 유닛 이름별로 combat에서 남긴 "남은 총 인구"를 차감하며 할당
        left_by_name = {}
        for k, v in list(losses_by_user_and_name.items()):
            (t_uid, name) = k
            if t_uid == uid:
                left_by_name[name] = v

        new_units = []
        for u in pdata.get("units", []):
            name = u["이름"]
            cur = int(u.get("인구", 0))
            if name in left_by_name:
                # 이 이름에 배정된 "남은 인구 총합"에서 하나씩 채워 넣음
                keep = min(cur, left_by_name[name])
                left_by_name[name] -= keep
                u["인구"] = keep
            else:
                # 전투에 참여 안 했거나 전멸 처리되었으면 0
                u["인구"] = 0
            if u["인구"] > 0:
                new_units.append(u)
        pdata["units"] = new_units
