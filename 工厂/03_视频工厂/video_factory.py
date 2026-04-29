# -*- coding: utf-8 -*-
"""
🎬 03_视频工厂 — 分镜图 → Seedance 2.0 视频
监听 storyboard-factory 输出的 episode 包，调用 Dreamina 首尾帧工具生成视频

流程:
  1. 读取 episode_XXX.json (含分镜图路径+video_prompt)
  2. 相邻两张分镜图作为首帧+尾帧
  3. 调用 Dreamina 首尾帧批量工具生成15s视频
  4. 输出视频片段 → assembly-factory/queue

用法:
  python video_factory.py                  # 处理所有待处理集
  python video_factory.py --daemon         # 守护模式
  python video_factory.py --episode 1      # 只处理指定集
"""
import argparse
import concurrent.futures
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── Paths ──
FACTORY_DIR = Path(__file__).parent       # 工厂/03_视频工厂/
VIDEO_QUEUE_IN = FACTORY_DIR / "queue"    # 来自02_图片工厂的分镜包
ASSEMBLY_QUEUE = FACTORY_DIR.parent / "04_音频合成工厂" / "queue"
OUTPUT_DIR = FACTORY_DIR / "output"
PROJECT_ROOT = FACTORY_DIR.parent.parent

# Dreamina 首尾帧工具 (工厂/03_视频工厂/Dreamina/)
DREAMINA_TOOL   = FACTORY_DIR / "Dreamina"
DREAMINA_SCRIPT = DREAMINA_TOOL / "dreamina_first_last_batch.py"
DREAMINA_FRAMES = DREAMINA_TOOL / "frames"
DREAMINA_OUTPUT = DREAMINA_TOOL / "output"
ACCOUNT_LOCKS_DIR = DREAMINA_OUTPUT / "account_locks"
ACCOUNTS_DIR = FACTORY_DIR / "即梦" / "Dreamina Pro Max" / "registered_accounts_usa"
READY_ACCOUNTS_FILE = ACCOUNTS_DIR / "accounts_fast0_ready.txt"
PREPARE_ACCOUNTS_SCRIPT = FACTORY_DIR / "prepare_dreamina_accounts.py"
FFMPEG = FACTORY_DIR.parent / "04_音频合成工厂" / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"

OUTPUT_DIR.mkdir(exist_ok=True)
ASSEMBLY_QUEUE.mkdir(exist_ok=True)
ACCOUNT_LOCKS_DIR.mkdir(parents=True, exist_ok=True)

# ── Novel Name ──
def _read_novel_name() -> str:
    try:
        import yaml
        cfg = PROJECT_ROOT / "config" / "config.yaml"
        d = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        if d.get("novel_name"): return d["novel_name"]
    except Exception:
        pass
    try:
        cfg2 = FACTORY_DIR.parent / "01_小说工厂" / "config.json"
        return json.loads(cfg2.read_text(encoding="utf-8")).get("novel_name", "禁蛊录")
    except Exception:
        return "禁蛊录"

NOVEL_NAME = _read_novel_name()

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [03_视频工厂] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(FACTORY_DIR / "video_factory.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


def find_accounts_file():
    """合并所有账号文件为一个主文件，确保新注册账号可被利用"""
    if not ACCOUNTS_DIR.exists():
        return None
    try:
        result = subprocess.run(
            [sys.executable, str(PREPARE_ACCOUNTS_SCRIPT), "--allow-no-state"],
            cwd=str(FACTORY_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if result.returncode == 0 and READY_ACCOUNTS_FILE.exists() and READY_ACCOUNTS_FILE.stat().st_size > 0:
            log.info(f"  📋 使用 Fast=0 ready账号池: {READY_ACCOUNTS_FILE}")
            return str(READY_ACCOUNTS_FILE)
        if result.returncode != 0:
            log.warning(f"  ⚠️ ready账号池整理失败，回退旧合并逻辑: {((result.stdout or '') + (result.stderr or ''))[-300:]}")
    except Exception as e:
        log.warning(f"  ⚠️ ready账号池整理异常，回退旧合并逻辑: {e}")

    txts = sorted(ACCOUNTS_DIR.glob("accounts_*.txt"), reverse=True)  # 新文件优先
    if not txts:
        return None

    # 合并所有文件内容，按邮算去重
    seen_emails = set()
    merged_lines = []
    for f in txts:
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            email = line.split("----")[0].strip()
            if email and email not in seen_emails:
                seen_emails.add(email)
                merged_lines.append(line)

    if not merged_lines:
        return None

    # 写入合并主文件
    master = ACCOUNTS_DIR / "_master_accounts.txt"
    master.write_text("\n".join(merged_lines), encoding="utf-8")
    log.info(f"  📋 合并账号池: {len(merged_lines)} 个 (来自 {len(txts)} 个文件)")
    return str(master)


# ── 并发控制 ──
DREAMINA_CONCURRENCY = int(os.environ.get("DREAMINA_CONCURRENCY", "5"))

def _clash_nodes_for_factory():
    """从 Clash API 读节点列表（供工厂预分配用）。"""
    try:
        import glob as _glob, re as _re
        cfg_paths = [str(Path.home() / ".config" / "clash" / "config.yaml"),
                     r"C:\Users\*\.config\clash\config.yaml"]
        api, secret = "", ""
        for p in cfg_paths:
            for f in _glob.glob(p):
                txt = Path(f).read_text(encoding="utf-8", errors="ignore")
                for ln in txt.splitlines():
                    if ln.startswith("external-controller:"):
                        api = "http://" + ln.split(":", 1)[1].strip()
                    if ln.startswith("secret:"):
                        secret = ln.split(":", 1)[1].strip().strip("\"'")
                if api:
                    break
            if api:
                break
        if not api:
            return []
        import requests as _req
        h = {"Authorization": f"Bearer {secret}"} if secret else {}
        r = _req.get(f"{api}/proxies/GLOBAL", headers=h, timeout=5,
                     proxies={"http": None, "https": None})
        skip_kw = ["剩余流量", "套餐到期", "🏡家", "DIRECT", "REJECT",
                   "♻️", "🔯", "🌍", "🎮", "📹", "🎥", "📺", "🐱"]
        nodes = [n for n in r.json().get("all", [])
                 if not any(k in n for k in skip_kw)]
        # 优先非香港节点（分散 IP 地域）
        preferred_order = ["新加坡", "日本", "美国", "英国", "台湾", "香港"]
        ordered = []
        for region in preferred_order:
            ordered += [n for n in nodes if region in n]
        ordered += [n for n in nodes if n not in ordered]
        return ordered
    except Exception as e:
        log.debug(f"[clash] 节点列表获取失败: {e}")
        return []

def _available_account_emails(accounts_file, n_needed):
    """从主账号文件中取 n_needed 个未使用的 fast_cost=0 账号 email。"""
    used = set()
    used_f = DREAMINA_OUTPUT / "used_accounts.txt"
    if used_f.exists():
        for ln in used_f.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                used.add(ln.split()[0].strip().lower())
    locked = {p.stem.strip().lower() for p in ACCOUNT_LOCKS_DIR.glob("*.lock") if "@" in p.stem}
    emails = []
    skip_kw = {"msToken", "passport_csrf", "sessionid"}  # 不是 email，是 cookie 名
    for ln in Path(accounts_file).read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        parts = [p.strip() for p in ln.split("----")]
        if len(parts) < 3:
            continue
        if "Sessionid=" not in ln:
            continue
        email = parts[0]
        try:
            fast_cost = int(parts[5]) if len(parts) > 5 and parts[5] else 0
        except (ValueError, IndexError):
            fast_cost = 0  # 新账号无此字段，默认视为免费
        if fast_cost != 0:
            continue
        email_key = email.lower()
        if email_key in used or email_key in locked:
            continue
        emails.append(email)
        if len(emails) >= n_needed:
            break
    return emails


def _account_lock_path(email):
    safe = "".join(ch if ch.isalnum() or ch in "._@-" else "_" for ch in str(email or "").strip())
    return ACCOUNT_LOCKS_DIR / f"{safe}.lock"


def _lock_account(email, shot_idx):
    if not email:
        return None
    lock_path = _account_lock_path(email)
    payload = {
        "email": email,
        "shot_idx": shot_idx,
        "locked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        with lock_path.open("x", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2))
        return lock_path
    except FileExistsError:
        return None


def _consume_account(email, reason, output_path=""):
    if not email:
        return
    used_f = DREAMINA_OUTPUT / "used_accounts.txt"
    with used_f.open("a", encoding="utf-8") as f:
        f.write(f"{email}\t{time.strftime('%Y-%m-%d %H:%M:%S')}\t{reason}:{output_path}\n")
    try:
        _account_lock_path(email).unlink(missing_ok=True)
    except Exception:
        pass


def generate_video_segment(first_frame, last_frame, prompt, output_path,
                            duration=15, shot_idx=0,
                            clash_node="", account_email="",
                            accounts_file=None):
    """
    调用 Dreamina 首尾帧工具生成单个视频片段（支持并发隔离）
    accounts_file: 预计算好的账号文件路径，避免并发时重复写 _master_accounts.txt
    Returns: True if success
    """
    if not accounts_file:
        accounts_file = find_accounts_file()
    if not accounts_file:
        log.error("❌ 未找到 Dreamina 账号文件")
        return False
    if not DREAMINA_SCRIPT.exists():
        log.error(f"❌ Dreamina 脚本不存在: {DREAMINA_SCRIPT}")
        return False
    lock_path = None
    if account_email:
        lock_path = _lock_account(account_email, shot_idx)
        if not lock_path:
            log.warning(f"  ⚠️ [shot_{shot_idx:02d}] 账号已被锁定，跳过: {account_email}")
            return False

    # ── per-shot 隔离目录（避免并发覆盖）──
    frames_dir = DREAMINA_FRAMES / f"shot_{shot_idx:02d}"
    out_dir    = DREAMINA_OUTPUT  / f"shot_{shot_idx:02d}"
    frames_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    first_dst = frames_dir / "first.png"
    last_dst  = frames_dir / "last.png"
    shutil.copy2(first_frame, first_dst)
    if last_frame and Path(last_frame).exists():
        shutil.copy2(last_frame, last_dst)
    else:
        shutil.copy2(first_frame, last_dst)

    env = os.environ.copy()
    env["DREAMINA_ACCOUNTS_FILE"] = accounts_file
    env["DREAMINA_FIRST_FRAME"]   = str(first_dst)
    env["DREAMINA_LAST_FRAME"]    = str(last_dst)
    env["DREAMINA_PROMPT"]        = prompt
    env["DREAMINA_DURATION"]      = str(duration)
    env["DREAMINA_MAX_ACCOUNTS"]  = "1"   # 并发模式：每 shot 1个预分配账号，无竞争
    env["DREAMINA_COUNTRY"]       = ""
    env["DREAMINA_FAST_COST"]     = "0"
    env["DREAMINA_OUTPUT_DIR"]    = str(out_dir)
    env["DREAMINA_HEADLESS"]      = "1"
    env["PYTHONIOENCODING"]         = "utf-8"   # 修复 Windows GBK UnicodeEncodeError
    # 并发关键：used_accounts + log 写入中央目录，不随 per-shot 目录丢失
    env["DREAMINA_USED_FILE"]     = str(DREAMINA_OUTPUT / "used_accounts.txt")
    env["DREAMINA_LOG_FILE"]      = str(DREAMINA_OUTPUT / "batch_log.jsonl")
    if clash_node:
        env["CLASH_NODE"] = clash_node      # 预分配节点
    if account_email:
        env["DREAMINA_EMAIL"] = account_email  # 预分配账号（严格过滤）

    log.info(f"  🎬 [shot_{shot_idx:02d}] node={clash_node or 'auto'} acct={account_email[:20] if account_email else 'auto'}")

    try:
        submit_time = time.time()
        result = subprocess.run(
            [sys.executable, str(DREAMINA_SCRIPT)],
            cwd=str(DREAMINA_TOOL),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )

        video_files = sorted(
            [p for p in out_dir.glob("*.mp4") if p.stat().st_mtime >= submit_time - 5],
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if video_files:
            shutil.move(str(video_files[0]), str(output_path))
            log.info(f"  ✅ [shot_{shot_idx:02d}] {output_path.name} ({video_files[0].stat().st_size // 1024}KB)")
            _consume_account(account_email, "SUCCESS", str(output_path))
            return True

        combined = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            log.warning(f"  ❌ [shot_{shot_idx:02d}] exit {result.returncode}: {combined[-400:]}")
        else:
            log.warning(f"  ⚠️ [shot_{shot_idx:02d}] 完成但无输出视频")

    except subprocess.TimeoutExpired:
        log.error(f"  ❌ [shot_{shot_idx:02d}] 超时 (10min)")
    except Exception as e:
        log.error(f"  ❌ [shot_{shot_idx:02d}] 错误: {e}")

    _consume_account(account_email, "CONSUMED_FAILED", f"shot_{shot_idx:02d}")
    return False


def process_episode(episode_file):
    """处理单集 — 所有 shot 并发生成（每个 shot 独立目录+账号+节点）"""
    data = json.loads(episode_file.read_text(encoding="utf-8"))
    ep_num = data["episode_num"]
    shots  = data["shots"]

    assembly_file = ASSEMBLY_QUEUE / f"episode_{ep_num:03d}.json"
    if assembly_file.exists():
        try:
            asm = json.loads(assembly_file.read_text(encoding="utf-8"))
            has_success = any(
                s.get("status") == "done" and s.get("video_path") and Path(s["video_path"]).exists()
                for s in asm.get("segments", [])
            )
            if has_success:
                log.info(f"⏭️ Episode {ep_num} already done ({asm.get('success_segments',0)}/{asm.get('total_segments',0)})")
                return True
            log.info(f"⚠️ Episode {ep_num} assembly全失败，重新生成")
        except Exception:
            pass

    log.info(f"🎬 处理 Episode {ep_num} ({len(shots)} shots，并发={DREAMINA_CONCURRENCY})")

    ep_dir = OUTPUT_DIR / NOVEL_NAME / f"episode_{ep_num:03d}"
    ep_dir.mkdir(parents=True, exist_ok=True)

    # ── 预分配 Clash 节点 + 账号（消除并发竞争）──
    accounts_file = find_accounts_file() or ""
    clash_nodes  = _clash_nodes_for_factory()
    # 每两个相邻shot之间产生1段视频，但最后一张图也要有结尾过渡
    # 实际：N张图=从shot[0]到shot[N-1]，需要N段视频（每张图本身做一个short clip）
    # 但dreamina首尾帧需要2张图，所以用相邻配对
    n_segments   = max(1, len(shots) - 1) if len(shots) >= 2 else 0

    pre_accounts: list = []
    if accounts_file:
        pre_accounts = _available_account_emails(accounts_file, n_segments * 2)
        log.info(f"  预分配 {len(pre_accounts)} 个账号 / {len(clash_nodes)} 个节点 for {n_segments} segments")

    # ── 构建任务列表 ──
    # 已完成的 segment 直接跳过
    pending_tasks = []   # (seg_num, shot_idx, first_frame, last_frame, prompt, seg_path)
    done_segments = []

    for i in range(n_segments):
        seg_num  = i + 1
        seg_path = ep_dir / f"segment_{seg_num:02d}.mp4"

        if seg_path.exists() and seg_path.stat().st_size > 50000:
            log.info(f"  ⏭️ Segment {seg_num} exists")
            done_segments.append({
                "segment": seg_num, "video_path": str(seg_path), "status": "done",
            })
            continue

        first_frame = shots[i].get("image_path")
        last_frame  = shots[i + 1].get("image_path")
        prompt      = shots[i].get("video_prompt", "Smooth cinematic motion, dark fantasy atmosphere")

        if not first_frame or not Path(first_frame).exists():
            log.warning(f"  ⚠️ Segment {seg_num}: missing first frame, skip")
            done_segments.append({"segment": seg_num, "status": "skipped_no_frame"})
            continue

        pending_tasks.append((seg_num, i, first_frame, last_frame, prompt, seg_path))

    results: dict[int, dict] = {s["segment"]: s for s in done_segments}

    if pending_tasks:
        t0 = time.time()
        log.info(f"  🚀 并发生成 {len(pending_tasks)} segments (max_workers={min(DREAMINA_CONCURRENCY, len(pending_tasks))})")

        def _run_one(task):
            seg_num, idx, first_frame, last_frame, prompt, seg_path = task
            # 每个 task 用独立节点（循环分配）和独立账号
            node    = clash_nodes[idx % len(clash_nodes)] if clash_nodes else ""
            account = pre_accounts[idx] if idx < len(pre_accounts) else ""
            ok = generate_video_segment(
                first_frame, last_frame, prompt, seg_path,
                shot_idx=idx, clash_node=node, account_email=account,
                accounts_file=accounts_file,  # 复用预计算结果，避免并发写竞态
            )
            return seg_num, ok, str(seg_path) if ok else None

        workers = min(DREAMINA_CONCURRENCY, len(pending_tasks))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_run_one, t): t[0] for t in pending_tasks}
            for fut in concurrent.futures.as_completed(futs):
                seg_num, ok, vpath = fut.result()
                results[seg_num] = {
                    "segment": seg_num,
                    "video_path": vpath,
                    "status": "done" if ok else "failed",
                    "backend": "dreamina" if ok else "failed",
                }
                status_icon = "✅" if ok else "❌"
                log.info(f"  {status_icon} Segment {seg_num} {'OK' if ok else 'FAILED'}")

        elapsed = time.time() - t0
        log.info(f"  ⏱️ 并发生成耗时 {elapsed:.0f}s ({len(pending_tasks)} segments)")

    # ── 汇总输出 ──
    video_segments = [results[k] for k in sorted(results)]
    success_segments = sum(1 for s in video_segments if s["status"] == "done")
    assembly_data = {
        "episode_num": ep_num,
        "novel": NOVEL_NAME,
        "segments": video_segments,
        "narration_texts": data.get("narration_texts", []),
        "total_segments": len(video_segments),
        "success_segments": success_segments,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "ready" if success_segments > 0 else "failed",
    }
    assembly_file.write_text(json.dumps(assembly_data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"  📦 Episode {ep_num}: {success_segments}/{len(video_segments)} → assembly queue")
    return True


def main():
    parser = argparse.ArgumentParser(description="🎬 03_视频工厂")
    parser.add_argument("--daemon", action="store_true", help="守护模式")
    parser.add_argument("--episode", type=int, default=0, help="只处理指定集")
    args = parser.parse_args()

    log.info("🎬 03_视频工厂启动")

    while True:
        # 扫描分镜队列 (来自 storyboard-factory 输出到 video-factory/queue)
        episode_files = sorted(VIDEO_QUEUE_IN.glob("episode_*.json"))
        if args.episode > 0:
            episode_files = [f for f in episode_files if f"_{args.episode:03d}" in f.name]

        pending = []
        for f in episode_files:
            data = json.loads(f.read_text(encoding="utf-8"))
            ep = data["episode_num"]
            out = ASSEMBLY_QUEUE / f"episode_{ep:03d}.json"
            if not out.exists():
                pending.append(f)
            else:
                # 验证至少有一个 segment 成功，否则重新处理
                try:
                    asm = json.loads(out.read_text(encoding="utf-8"))
                    has_ok = any(
                        s.get("status") == "done" and s.get("video_path") and Path(s["video_path"]).exists()
                        for s in asm.get("segments", [])
                    )
                    if not has_ok:
                        pending.append(f)
                except Exception:
                    pending.append(f)

        if pending:
            log.info(f"📋 发现 {len(pending)} 集待处理")
            for ef in pending:
                process_episode(ef)
        else:
            if not args.daemon:
                log.info("✅ 所有集已处理")
                break
            log.info("⏳ 无待处理集，等待60秒...")

        if not args.daemon:
            break
        time.sleep(60)


if __name__ == "__main__":
    main()
