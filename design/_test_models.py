"""验证「接入多个模型 + 角色各选一个」的完整链路。

    python design/_test_models.py

覆盖：
  1. 添加两个模型 → config.json 里有两条
  2. 设置页把它们都列出来，Key 只出掩码
  3. 角色分配：shiori→模型A，miku→模型B
  4. 解析：两个角色拿到各自的 base_url / api_key / model
  5. 未分配的角色回落到第一个模型
  6. 删除模型后，引用它的角色分配被清掉
  7. 明文 Key 不出现在任何页面
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novelhub import personas as cast            # noqa: E402
from novelhub.chat import load_chat_config      # noqa: E402
from novelhub.config import load_config         # noqa: E402
from novelhub.webapp.app import create_app      # noqa: E402

CFG = ROOT / "config.json"
BACKUP = ROOT / "config.json.modelsbak"
fails: list[str] = []

KEY_A = "sk-model-alpha-AAAA1111"
KEY_B = "sk-model-beta-BBBB2222"


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        fails.append(label)


def read_cfg() -> dict:
    return json.loads(CFG.read_text(encoding="utf-8"))


def main() -> int:
    shutil.copy2(CFG, BACKUP)
    try:
        app = create_app()
        app.config["PROPAGATE_EXCEPTIONS"] = True
        client = app.test_client()

        # 从干净状态开始
        raw = read_cfg()
        raw.setdefault("chat", {})["models"] = []
        raw["chat"]["persona_models"] = {}
        raw["chat"].pop("api_key", None)
        raw["chat"].pop("model", None)
        CFG.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

        # 1) 添加模型 A
        print("[1] 添加两个模型")
        r = client.post("/settings", data={
            "section": "model_add", "provider": "zhipu",
            "label": "智谱 Flash", "model": "glm-4-flash",
            "base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key": KEY_A,
        })
        check(r.status_code in (302, 303), "添加模型 A 成功")

        # 添加模型 B
        r = client.post("/settings", data={
            "section": "model_add", "provider": "deepseek",
            "label": "DeepSeek", "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1", "api_key": KEY_B,
        })
        check(r.status_code in (302, 303), "添加模型 B 成功")

        models = read_cfg()["chat"].get("models") or []
        check(len(models) == 2, f"config 里有 2 个模型（实际 {len(models)}）")
        ids = [m["id"] for m in models]
        check(all(ids), "每个模型都有 id")
        check(len(set(ids)) == 2, "id 不重复")

        # 2) 页面列出两个模型 + 掩码
        print("[2] 设置页展示")
        page = client.get("/settings").get_data(as_text=True)
        check("智谱 Flash" in page, "列出「智谱 Flash」")
        check("DeepSeek" in page, "列出「DeepSeek」")
        check(KEY_A not in page, "模型 A 明文 Key 不在页面")
        check(KEY_B not in page, "模型 B 明文 Key 不在页面")
        check(f"{KEY_A[:3]}…{KEY_A[-4:]}" in page, "模型 A 显示掩码")
        check(f"{KEY_B[:3]}…{KEY_B[-4:]}" in page, "模型 B 显示掩码")

        # 3) 角色分配
        print("[3] 角色分配")
        id_a, id_b = ids[0], ids[1]
        form = {"section": "chat"}
        for p in cast.list_all():
            form[f"model_{p.id}"] = ""
        form["model_shiori"] = id_a
        form["model_miku"] = id_b
        r = client.post("/settings", data=form)
        check(r.status_code in (302, 303), "保存分配成功")
        pm = read_cfg()["chat"].get("persona_models") or {}
        check(pm.get("shiori") == id_a, "shiori 绑定模型 A")
        check(pm.get("miku") == id_b, "miku 绑定模型 B")

        # 4) 解析：各自拿到自己的 Key/地址/模型
        print("[4] 运行时解析")
        cfg = load_config()
        c_a = load_chat_config(cfg, "zhipu", "shiori")
        c_b = load_chat_config(cfg, "deepseek", "miku")
        c_r = load_chat_config(cfg, "zhipu", "rin")
        print(f"    shiori: model={c_a.model!r} base={c_a.base_url!r} key={c_a.api_key[-4:]}")
        print(f"    miku  : model={c_b.model!r} base={c_b.base_url!r} key={c_b.api_key[-4:]}")
        print(f"    rin   : model={c_r.model!r} base={c_r.base_url!r} key={c_r.api_key[-4:]}")
        check(c_a.model == "glm-4-flash" and c_a.api_key == KEY_A, "shiori 用模型 A 全套配置")
        check(c_b.model == "deepseek-chat" and c_b.api_key == KEY_B, "miku 用模型 B 全套配置")
        check(c_r.model == "glm-4-flash" and c_r.api_key == KEY_A, "rin 回落第一个模型")

        # 5) 删除模型 B → miku 的绑定应被清掉
        print("[5] 删除模型")
        r = client.post("/settings", data={"section": "model_del", "model_id": id_b})
        check(r.status_code in (302, 303), "删除模型 B 成功")
        left = read_cfg()["chat"].get("models") or []
        check(len(left) == 1, f"只剩 1 个模型（实际 {len(left)}）")
        pm2 = read_cfg()["chat"].get("persona_models") or {}
        check("miku" not in pm2, "miku 的失效绑定已清除")

        # 6) 空模型时页面应提示
        print("[6] 清空后提示")
        client.post("/settings", data={"section": "model_del", "model_id": id_a})
        page2 = client.get("/settings").get_data(as_text=True)
        check("还没有接入任何模型" in page2, "空列表显示引导文案")

    finally:
        shutil.move(str(BACKUP), str(CFG))
        print("\nconfig.json 已还原")

    print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败: {fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
