#!/bin/bash
# 마음돌봄 — systemd 유닛 설치 헬퍼 (XAVIER_INSTALL_GUIDE.md §13)
#
# 동작:
#   1. /etc/systemd/system/ 에 mindcare-llama.service, mindcare-hri.service 배치
#   2. /var/log/mindcare-*.log 준비 (소유자 = user)
#   3. /etc/logrotate.d/mindcare 배치
#   4. systemctl daemon-reload
#   5. systemd-analyze verify (구문 검증, 시작 X)
#
# 검증 후 사용자가 수동으로:
#   sudo systemctl start mindcare-llama
#   journalctl -u mindcare-llama -f             # 모델 로드 ~30-60s 확인
#   sudo systemctl start mindcare-hri
#   systemctl status mindcare-{llama,hri}
#
# 부팅 자동 시작 활성화 (검증 통과 시):
#   sudo systemctl enable mindcare-llama mindcare-hri

set -e

SCRIPT_DIR="$( cd "$(dirname "${BASH_SOURCE[0]}")" && pwd )"
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR] sudo 로 실행: sudo bash $0${NC}"
    exit 1
fi

echo -e "${GRN}[1/5] 유닛 파일 설치${NC}"
install -m 0644 "$SCRIPT_DIR/mindcare-llama.service" /etc/systemd/system/
install -m 0644 "$SCRIPT_DIR/mindcare-hri.service"   /etc/systemd/system/
echo "  → /etc/systemd/system/mindcare-{llama,hri}.service"

echo -e "${GRN}[2/5] 로그 파일 준비${NC}"
mkdir -p /var/log
touch /var/log/mindcare-llama.log /var/log/mindcare-hri.log
chown user:user /var/log/mindcare-llama.log /var/log/mindcare-hri.log
chmod 0644 /var/log/mindcare-llama.log /var/log/mindcare-hri.log
echo "  → /var/log/mindcare-{llama,hri}.log (owner=user)"

echo -e "${GRN}[3/5] logrotate 설치${NC}"
install -m 0644 "$SCRIPT_DIR/mindcare.logrotate" /etc/logrotate.d/mindcare
echo "  → /etc/logrotate.d/mindcare"

echo -e "${GRN}[4/5] systemctl daemon-reload${NC}"
systemctl daemon-reload

echo -e "${GRN}[5/5] systemd-analyze verify (구문 검증)${NC}"
if systemd-analyze verify /etc/systemd/system/mindcare-llama.service; then
    echo "  ✔ mindcare-llama.service OK"
else
    echo -e "  ${YLW}⚠ mindcare-llama.service 경고${NC}"
fi
if systemd-analyze verify /etc/systemd/system/mindcare-hri.service; then
    echo "  ✔ mindcare-hri.service OK"
else
    echo -e "  ${YLW}⚠ mindcare-hri.service 경고${NC}"
fi

echo ""
echo -e "${GRN}설치 완료.${NC} 다음 단계 (수동):"
echo "  sudo systemctl start mindcare-llama"
echo "  journalctl -u mindcare-llama -f      # 모델 로드 ~30-60s"
echo "  sudo systemctl start mindcare-hri"
echo "  systemctl status mindcare-{llama,hri}"
echo ""
echo "부팅 자동 시작 (검증 OK 후):"
echo "  sudo systemctl enable mindcare-llama mindcare-hri"
