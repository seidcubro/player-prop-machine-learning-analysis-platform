from pathlib import Path

path = Path(r".\services\training\train.py")
text = path.read_text(encoding="utf-8-sig")

text = text.replace(
    """"recs_mean": r.get("recs_mean", 0),
        "recs_trend": r.get("recs_trend", 0),""",
    """"aux_mean": r.get("aux_mean", r.get("recs_mean", 0)),
        "aux_trend": r.get("aux_trend", r.get("recs_trend", 0)),
        "recs_mean": r.get("aux_mean", r.get("recs_mean", 0)),
        "recs_trend": r.get("aux_trend", r.get("recs_trend", 0)),"""
)

path.write_text(text, encoding="utf-8")
print("Patched train.py to use aux_mean/aux_trend with backward compatibility")
