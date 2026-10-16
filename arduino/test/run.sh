#!/bin/bash
# 펌웨어 호스트 테스트: 보드 없이 명령 처리·타이머·CO2·창문 규칙을 검사한다.
set -e
cd "$(dirname "$0")"
out=$(mktemp -d)
g++ -std=c++17 -Wall -Wextra -Werror -Wno-unused-parameter -Istubs firmware_test.cpp -o "$out/firmware_test"
"$out/firmware_test"
