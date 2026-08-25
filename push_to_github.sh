#!/usr/bin/env bash
# 推送 global-news-radar 到 GitHub（token 从环境变量读取，勿硬编码）
# 用法: export GITHUB_TOKEN=<你的token> && ./push_to_github.sh
set -euo pipefail
: "${GITHUB_TOKEN:?请先 export GITHUB_TOKEN}"
USER_LOGIN="Jssuck"
REPO="global-news-radar"

# 1. 若仓库不存在则创建（需要 token 有 Administration 写权限；或你已手动建仓则跳过）
code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $GITHUB_TOKEN" "https://api.github.com/repos/$USER_LOGIN/$REPO")
if [ "$code" != "200" ]; then
  echo "仓库不存在，尝试创建（public）..."
  curl -s -X POST -H "Authorization: Bearer $GITHUB_TOKEN" -H "Content-Type: application/json" \
    https://api.github.com/user/repos \
    -d "{\"name\":\"$REPO\",\"private\":false,\"description\":\"Self-hosted open-source (MIT) platform monitoring mainstream media worldwide 24/7 — fetch on publish, geo-block hints, three-stage rule+LLM cleaning pipeline.\",\"has_issues\":true,\"has_wiki\":false,\"auto_init\":false}" \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('html_url') or d.get('message'))"
fi

# 2. 推送 main 与 mvp
git remote remove origin 2>/dev/null || true
git remote add origin "https://x-access-token:${GITHUB_TOKEN}@github.com/$USER_LOGIN/$REPO.git"
git push -u origin main
git push -u origin mvp
git remote remove origin   # 移除含 token 的 remote，避免泄漏
echo "完成: https://github.com/$USER_LOGIN/$REPO"
