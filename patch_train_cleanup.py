from pathlib import Path
import re

path = Path(r".\services\training\train.py")
text = path.read_text(encoding="utf-8-sig")

text = re.sub(
    r'model_type"\s*:\s*"RandomForestRegressor"',
    'model_type": type(model).__name__',
    text
)

text = text.replace(
    'MODEL_NAME = os.getenv("MODEL_NAME", "rf_v1")',
    'MODEL_NAME = model_name'
)

path.write_text(text, encoding="utf-8")
print("Patched train.py cleanup")
