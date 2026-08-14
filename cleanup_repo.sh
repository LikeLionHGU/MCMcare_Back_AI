#!/bin/bash
# 저장소에서 문서를 전부 빼고 코드만 남긴다.
#
# 중요: git rm --cached 는 '저장소에서만' 빼는 것이고
#       내 맥의 파일은 그대로 남는다. 문서는 계속 볼 수 있다.

cd ~/Desktop/mcm_crawler || exit 1

echo "저장소에서 제외하는 중…"

# 팀 내부 문서
git rm --cached -q "현황.md"                2>/dev/null
git rm --cached -q "챗봇_해설.md"            2>/dev/null
git rm --cached -q "서버_이해하기.md"         2>/dev/null
git rm --cached -q "배포_가이드.md"           2>/dev/null
git rm --cached -q "시연_시나리오.md"         2>/dev/null
git rm --cached -q "통합메모_광은님.md"        2>/dev/null
git rm --cached -q "팀메시지_보낼것.md"        2>/dev/null
git rm --cached -q "팀공유_진행상황.md"        2>/dev/null
git rm --cached -q "팀공유_챗봇_0812.md"      2>/dev/null

# 작업 중 만든 발췌본 — as_chatbot.py 안에 이미 있는 내용
git rm --cached -q "openai_연동부분.md"       2>/dev/null
git rm --cached -q "openai_연동부분.py"       2>/dev/null

# README — 나중에 정리해서 다시 올릴 예정
git rm --cached -q "README.md"              2>/dev/null

# 이 스크립트 자신도 저장소에 있을 이유가 없다
git rm --cached -q "cleanup_repo.sh"        2>/dev/null

# 앞으로 'git add .' 할 때 다시 딸려 올라가지 않게 등록
cat >> .gitignore << 'EOF'

# ── 문서는 로컬에만 둔다 ─────────────────────────────────
# 나중에 올리려면:  git add -f README.md
README.md
현황.md
챗봇_해설.md
서버_이해하기.md
배포_가이드.md
시연_시나리오.md
통합메모_광은님.md
팀메시지_보낼것.md
팀공유_진행상황.md
팀공유_챗봇_0812.md
openai_연동부분.md
openai_연동부분.py
cleanup_repo.sh
EOF

git add .gitignore
git commit -m "chore: 문서를 저장소에서 제외하고 코드만 유지"
git push

echo ""
echo "=========================================="
echo "저장소에 남은 파일:"
git ls-files
echo "=========================================="
echo "※ 위에서 뺀 문서들은 내 맥에 그대로 있습니다."
echo "※ 나중에 README를 올리려면:  git add -f README.md"
