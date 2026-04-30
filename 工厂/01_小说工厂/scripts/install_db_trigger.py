"""
安装 SQLite 触发器：每次 ToonFlow 写入新 t_outline 时，
自动删除同 project 下所有「无 t_script 引用」的孤儿 outline。
"""
import sqlite3, os

db = os.path.join(os.environ["APPDATA"], "toonflow-app", "db.sqlite")
conn = sqlite3.connect(db)
cur = conn.cursor()

TRIGGER_NAME = "auto_cleanup_orphan_outlines"

# 删旧触发器（幂等）
cur.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME}")

# 创建新触发器
cur.execute(f"""
CREATE TRIGGER {TRIGGER_NAME}
AFTER INSERT ON t_outline
FOR EACH ROW
BEGIN
    DELETE FROM t_outline
    WHERE projectId = NEW.projectId
      AND id != NEW.id
      AND id NOT IN (
          SELECT outlineId FROM t_script
          WHERE outlineId IS NOT NULL
            AND projectId = NEW.projectId
      );
END
""")

conn.commit()

# 验证
cur.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND name=?", (TRIGGER_NAME,))
row = cur.fetchone()
if row:
    print(f"✅ 触发器已安装: {row[0]}")
else:
    print("❌ 触发器安装失败")

# 顺手再清一次残余孤儿（首次安装）
cur.execute("""
    DELETE FROM t_outline
    WHERE id NOT IN (
        SELECT outlineId FROM t_script WHERE outlineId IS NOT NULL
    )
""")
deleted = cur.rowcount
conn.commit()
print(f"✅ 初始清理: 删除 {deleted} 条孤儿 outline")

conn.close()
print("完成。ToonFlow 重启后无需任何操作，孤儿 tab 从此自动消失。")
