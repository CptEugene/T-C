import pandas as pd

def load_units_from_excel(file_path="units.xlsx"):
    df = pd.read_excel(file_path)
    units = {}

    for idx, row in df.iterrows():
        unit_id = idx + 1  # 엑셀의 행 번호를 ID로 사용 (1부터 시작)
        units[unit_id] = {
            "id": unit_id,
            "시대": row.get("시대"),
            "티어": row.get("티어"),
            "이름": row.get("이름"),
            "병종": row.get("병종"),
            "유형": row.get("유형"),
            "공격유형": row.get("공격유형"),
            "공격력": row.get("일반공격"),
            "방어력": row.get("일반방어"),
            "특수공격": row.get("특수공격"),
            "특수방어": row.get("특수방어"),
            "인구": row.get("인구"),
            "체력": row.get("체력"),
            "요구능력": row.get("요구능력"),
            "요구능력치": row.get("요구능력치")
        }

    return units