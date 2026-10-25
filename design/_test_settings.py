"""验证设置页：模型列表（可加多个）+ 角色按模型 id 分配 + Key 不外泄。

    python design/_test_settings.py

这是重构后的版本（旧版测的是「单 Key + 角色存模型名」）。
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
BACKUP = ROOT / "config.json.settest"
KEY_A = "sk-alpha-AAAA1111"
KEY_B = "sk-beta-BBBB2222"
fails: list[str] = []


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

        # 干净起点
        raw = read_cfg()
        raw.setdefault("chat", {})
        raw["chat"]["models"] = []
        raw["chat"]["persona_models"] = {}
        raw["chat"]["custom_personas"] = []
        raw["chat"].pop("api_key", None)
        raw["chat"].pop("model", None)
        raw["chat"].pop("custom_persona", None)
        CFG.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

        # 1) 加两个模型
        print("[1] 添加模型")
        client.post("/settings", data={
            "section": "model_add", "provider": "zhipu", "label": "智谱 Flash",
            "model": "glm-4-flash", "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key": KEY_A})
        client.post("/settings", data={
            "section": "model_add", "provider": "deepseek", "label": "DeepSeek",
            "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1",
            "api_key": KEY_B})
        models = read_cfg()["chat"].get("models") or []
        check(len(models) == 2, f"config 里 2 个模型（实际 {len(models)}）")

        # 2) 角色按模型 id 分配
        print("[2] 角色分配")
        ids = [m["id"] for m in models]
        zp = next(m["id"] for m in models if m["provider"] == "zhipu")
        ds = next(m["id"] for m in models if m["provider"] == "deepseek")
        form = {"section": "chat"}
        for p in cast.list_all():
            form[f"model_{p.id}"] = ""
        form["model_shiori"] = zp
        form["model_miku"] = ds
        client.post("/settings", data=form)
        pm = read_cfg()["chat"].get("persona_models") or {}
        check(pm.get("shiori") == zp, "shiori 绑定智谱")
        check(pm.get("miku") == ds, "miku 绑定 DeepSeek")

        # 3) 解析出各自的整套配置
        print("[3] 运行时解析")
        cfg = load_config()
        a = load_chat_config(cfg, "zhipu", "shiori")
        b = load_chat_config(cfg, "deepseek", "miku")
        print(f"    shiori: {a.model} @ {a.base_url}")
        print(f"    miku  : {b.model} @ {b.base_url}")
        check(a.model == "glm-4-flash" and a.api_key == KEY_A, "shiori 用模型 A 全套")
        check(b.model == "deepseek-chat" and b.api_key == KEY_B, "miku 用模型 B 全套")

        # 4) 页面结构
        print("[4] 页面检查")
        page = client.get("/settings").get_data(as_text=True)
        check("接入的模型" in page, "有模型列表区块")
        check("智谱 Flash" in page and "DeepSeek" in page, "两个模型都列出")
        check("每个角色用哪个模型" in page, "有角色分配区块")
        check('name="temperature"' not in page, "温度字段已移除")
        check('name="max_tokens"' not in page, "max_tokens 已移除")

        # 5) 明文 Key 不外泄
        print("[5] Key 安全")
        check(KEY_A not in page, "模型 A 明文不在页面")
        check(KEY_B not in page, "模型 B 明文不在页面")
        check(f"{KEY_A[:3]}…{KEY_A[-4:]}" in page, "模型 A 显示掩码")

        # 6) 删模型会清掉角色绑定
        print("[6] 删除模型")
        client.post("/settings", data={"section": "model_del", "model_id": ds})
        left = read_cfg()["chat"].get("models") or []
        pm2 = read_cfg()["chat"].get("persona_models") or {}
        check(len(left) == 1, "只剩 1 个模型")
        check("miku" not in pm2, "miku 的失效绑定已清除")

        # 7) 环境变量隔离
        print("[7] 环境变量隔离")
        import os
        os.environ["ZHIPU_API_KEY"] = "sk-ENV-SHOULD-NOT-BE-USED"
        try:
            r2 = read_cfg()
            r2["chat"]["models"] = [{"id": "m_e", "label": "e", "provider": "zhipu",
                                     "base_url": "https://x/v1", "model": "glm-4-flash",
                                     "api_key": "${ZHIPU_API_KEY}"}]
            r2["chat"]["persona_models"] = {}
            r2["chat"].pop("use_env_key", None)
            CFG.write_text(json.dumps(r2, ensure_ascii=False, indent=2), encoding="utf-8")
            c = load_chat_config(load_config(), "zhipu", "shiori")
            check("ENV-SHOULD-NOT" not in c.api_key, "默认不展开 ${VAR}")
        finally:
            os.environ.pop("ZHIPU_API_KEY", None)

    finally:
        shutil.move(str(BACKUP), str(CFG))
        print("\nconfig.json 已还原")

    print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败: {fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
