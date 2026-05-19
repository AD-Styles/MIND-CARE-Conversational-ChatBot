"""api_gateway_node.py — uvicorn 으로 FastAPI 띄우는 ROS 진입점.

ROS 패키지 entry_points 가 이 함수를 가리키므로 `ros2 run mind_care_api
api_gateway_node` 한 번에 시작.
"""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn

from .app import create_app

log = logging.getLogger("mind_care_api.api_gateway_node")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=os.environ.get("MIND_CARE_HOST", "0.0.0.0"))
    p.add_argument("--port", type=int,
                    default=int(os.environ.get("MIND_CARE_PORT", "8000")))
    p.add_argument("--elder-id", default=os.environ.get("MIND_CARE_ELDER", "elder_01"))
    p.add_argument("--db-path",  default=os.environ.get("MIND_CARE_DB"))
    p.add_argument("--dev-open", action="store_true",
                    default=os.environ.get("MIND_CARE_DEV_OPEN", "0") == "1",
                    help="인증 우회 (절대 운영 X — 개발용). "
                         "MIND_CARE_DEV_OPEN=1 env 로도 켤 수 있음")
    args, _unknown = p.parse_known_args()

    logging.basicConfig(level=logging.INFO,
                        format="[%(levelname)s] %(name)s — %(message)s")

    app = create_app(db_path=args.db_path, elder_id=args.elder_id,
                     dev_open=args.dev_open)

    log.info("uvicorn 시작 — http://%s:%d/docs", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port,
                log_level="info", access_log=False)


if __name__ == "__main__":
    main()
