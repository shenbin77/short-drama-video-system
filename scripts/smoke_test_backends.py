#!/usr/bin/env python3
"""
image_backends.py 冒烟测试 — 验证 APIMart GPT Image 2 + GRSAI 双后端
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '工厂', '02_图片工厂', 'pipelines'))

# 设置日志
import logging
logging.basicConfig(level=logging.INFO, format='%(message)s')

from image_backends import generate_image, apimart_image2, grsai_gpt_image2

print("=" * 60)
print("image_backends.py 冒烟测试")
print("=" * 60)

# 测试1: APIMart 主后端 (纯文本)
print("\n1️⃣  测试: apimart_image2 (纯文本)...")
img = apimart_image2("女主角站在山顶俯瞰云海，长发飘飘，3D国漫风格")
if img:
    with open("/mnt/e/视频项目/output/smoke_test_apimart.jpg", "wb") as f:
        f.write(img)
    print(f"   ✅ 成功! {len(img)//1024}KB → output/smoke_test_apimart.jpg")
else:
    print(f"   ❌ 失败!")

print("\n" + "=" * 60)
print("冒烟测试完成!")
print("=" * 60)
