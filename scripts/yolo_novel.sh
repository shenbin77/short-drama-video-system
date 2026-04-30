#!/bin/bash
# yolo_novel.sh — 从config.yaml读取Key，自动启动小说工厂
# WSL专用：cd /mnt/e/视频项目 && ./scripts/yolo_novel.sh [--chapters 10]

set -e

PROJECT_DIR="/mnt/e/视频项目"
CONFIG_FILE="$PROJECT_DIR/config/config.yaml"
NOVEL_FACTORY="$PROJECT_DIR/工厂/01_小说工厂/novel_factory.py"

# 自动从config.yaml读取DeepSeek API Key
API_KEY=$(python3 -c "
import yaml, json
cfg = yaml.safe_load(open('$CONFIG_FILE'))
print(cfg.get('deepseek', {}).get('api_key', ''))
" 2>/dev/null)

if [ -z "$API_KEY" ] || [ "$API_KEY" = "None" ]; then
    # 尝试从环境变量读取
    if [ -n "$DEEPSEEK_API_KEY" ]; then
        API_KEY="$DEEPSEEK_API_KEY"
    else
        echo "❌ 无法读取DeepSeek API Key"
        echo "   请手动设置: export DEEPSEEK_API_KEY='sk-your-key'"
        exit 1
    fi
fi

export DEEPSEEK_API_KEY="$API_KEY"
echo "✅ DeepSeek API Key已设置 (长度: ${#API_KEY})"

# 切换到项目目录
cd "$PROJECT_DIR"

# 参数
CHAPTERS=${1:-"--chapters 10"}
if [[ "$1" == --* ]]; then
    EXTRA_ARGS="$@"
else
    EXTRA_ARGS="--chapters ${1:-10}"
fi

echo "🚀 启动小说工厂..."
echo "  参数: $EXTRA_ARGS"
echo "═══════════════════════════════════════════════"

python3 "$NOVEL_FACTORY" $EXTRA_ARGS

echo ""
echo "✅ 小说工厂执行完成"
echo "  输出: $PROJECT_DIR/工厂/01_小说工厂/output/禁蛊录/"
echo "  队列: $PROJECT_DIR/工厂/01_小说工厂/queue/"
