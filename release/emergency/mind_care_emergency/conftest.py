# pytest conftest — `mind_care_emergency` 패키지가 이 디렉터리 바로 아래에 있어
# 동일 디렉터리에서 `pytest` 만 쳐도 자동 인식되도록 sys.path 추가.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
