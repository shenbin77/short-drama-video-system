#!/usr/bin/env python3
"""测试 APIMart GPT Image 2 API 连通性"""
import requests, json, time, base64

API_KEY = "sk-S8AYk1kic8Dihe7O5Do79VZqOT23d54QokCE9RL6BvEjvbCT"
BASE = "https://api.apimart.ai"

# 1. 提交生图任务
payload = {
    "model": "gpt-image-2",
    "prompt": "一只3D国漫风格的女主角，长发飘逸，站在山顶俯瞰云海，唯美光影",
    "size": "9:16",
    "resolution": "1k",
    "n": 1,
}

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

print("=" * 60)
print("1️⃣  提交生图任务...")
r = requests.post(f"{BASE}/v1/images/generations", json=payload, headers=headers, timeout=30)
print(f"   状态码: {r.status_code}")
print(f"   响应: {json.dumps(r.json(), ensure_ascii=False, indent=2)[:500]}")

data = r.json()
if data.get("code") != 200:
    print("❌ 提交失败!")
    exit(1)

task_id = data["data"][0]["task_id"]
print(f"   任务ID: {task_id}")

# 2. 轮询结果
print(f"\n2️⃣  轮询结果 (每5秒)...")
for i in range(60):
    time.sleep(5)
    pr = requests.get(f"{BASE}/v1/tasks/{task_id}", headers=headers, timeout=30)
    pr_data = pr.json()
    
    status = pr_data.get("data", {}).get("status", "")
    progress = pr_data.get("data", {}).get("progress", 0)
    
    if i % 2 == 0:
        print(f"   第{i+1}次轮询: status={status}, progress={progress}%")
    
    if status == "completed":
        images = pr_data.get("data", {}).get("result", {}).get("images", [])
        if images:
            img_url = images[0]["url"][0]
            print(f"\n   ✅ 图片生成成功!")
            print(f"    URL: {img_url}")
            
            # 下载图片
            img_r = requests.get(img_url, timeout=30)
            print(f"    图片大小: {len(img_r.content)//1024}KB")
            
            # 保存
            with open("/mnt/e/视频项目/output/test_apimart_result.jpg", "wb") as f:
                f.write(img_r.content)
            print(f"    已保存到: /mnt/e/视频项目/output/test_apimart_result.jpg")
            break
        else:
            print(f"   ⚠️ 完成但无图片数据")
        break
    
    elif status == "failed":
        error = pr_data.get("data", {}).get("error", {}).get("message", "unknown")
        print(f"   ❌ 生成失败: {error}")
        break

print("\n" + "=" * 60)
print("测试完成!")
