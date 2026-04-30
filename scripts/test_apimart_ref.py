#!/usr/bin/env python3
"""测试 APIMart GPT Image 2 参考图功能 — 用参考图保持角色一致性"""
import requests, json, time, base64

API_KEY = "sk-S8AYk1kic8Dihe7O5Do79VZqOT23d54QokCE9RL6BvEjvbCT"
BASE = "https://api.apimart.ai"

# 读取上一张生成的图作为参考图，测试同一角色能否保持一致性
ref_path = "/mnt/e/视频项目/output/test_apimart_result.jpg"
with open(ref_path, "rb") as f:
    ref_b64 = base64.b64encode(f.read()).decode("utf-8")

print("=" * 60)
print("1️⃣  提交「带参考图」生图任务...")
print(f"   参考图: {ref_path} ({len(ref_b64)//1024}KB base64)")

payload = {
    "model": "gpt-image-2",
    "prompt": "同一女主角，近景特写，她转身回眸微笑，长发随风飘动，背景是夕阳下的云海",
    "size": "9:16",
    "resolution": "1k",
    "n": 1,
    "image_urls": [f"data:image/jpeg;base64,{ref_b64}"],
}

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

r = requests.post(f"{BASE}/v1/images/generations", json=payload, headers=headers, timeout=30)
print(f"   状态码: {r.status_code}")
data = r.json()
print(f"   响应: {json.dumps(data, ensure_ascii=False, indent=2)[:500]}")

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
            img_r = requests.get(img_url, timeout=30)
            print(f"    图片大小: {len(img_r.content)//1024}KB")
            with open("/mnt/e/视频项目/output/test_apimart_ref_result.jpg", "wb") as f:
                f.write(img_r.content)
            print(f"    已保存到: output/test_apimart_ref_result.jpg")
        break
    elif status == "failed":
        error = pr_data.get("data", {}).get("error", {}).get("message", "unknown")
        print(f"   ❌ 生成失败: {error}")
        break

print("\n" + "=" * 60)
print("参考图测试完成!")
