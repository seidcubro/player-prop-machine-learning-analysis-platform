from pathlib import Path
import re

path = Path(r".\services\api\app\routes\jobs.py")
text = path.read_text(encoding="utf-8-sig")

pattern = re.compile(
    r'allowed_by_market = \{.*?# others isolated for now',
    re.DOTALL
)

replacement = '''allowed_by_market = {
        # receiving
        "recs": [],
        "rec_yds": ["recs"],
        "rec_td": ["recs"],

        # rushing
        "rush_att": [],
        "rush_yds": ["rush_att"],
        "rush_td": ["rush_att"],

        # passing
        "pass_att": [],
        "pass_completions": ["pass_att"],
        "pass_yds": ["pass_att"],
        "pass_td": ["pass_att", "pass_completions"],
        "pass_ints": ["pass_att", "pass_completions"],

        # others isolated for now'''

text = pattern.sub(replacement, text, count=1)

path.write_text(text, encoding="utf-8")
print("Patched jobs.py cleanup graph")
