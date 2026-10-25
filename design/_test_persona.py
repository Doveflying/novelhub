"""验证自建角色：可以共存多个、都有大管家能力、推荐分配正确。

    python design/_test_persona.py
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
BACKUP = ROOT / "config.json.personabak"
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
        raw.setdefault("chat", {})["models"] = []
        raw["chat"]["persona_models"] = {}
        raw["chat"]["custom_personas"] = []
        raw["chat"].pop("custom_persona", None)
        CFG.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

        # 1) 加两个自建角色 —— 必须能共存
        print("[1] 加两个自建角色（共存）")
        for nm, voice in [("白洲千夏", "冷淡、话少，用「……」，叫他「你」。"),
                          ("小猫铃", "爱撒娇，句尾带「喵」，叫他「主人」。")]:
            r = client.post("/settings", data={
                "section": "persona", "p_name": nm, "p_role": "大管家",
                "p_jp": "", "p_voice": voice, "p_greeting": "在。", "p_brief": "测试",
            })
            check(r.status_code in (302, 303), f"加入「{nm}」")

        customs = read_cfg()["chat"].get("custom_personas") or []
        check(len(customs) == 2, f"config 里有 2 个自建角色（实际 {len(customs)}）")
        names = [c["name"] for c in customs]
        check("白洲千夏" in names and "小猫铃" in names, "两个名字都在")
        check(len({c["id"] for c in customs}) == 2, "两个 id 不同")

        # 2) 运行时：两个都能取到，且都带大管家能力
        print("[2] 运行时角色与大管家能力")
        cfg = load_config()
        ps = cast.custom_personas(cfg)
        check(len(ps) == 2, "custom_personas() 返回 2 个")
        boss = cast.PERSONAS["miyagi"]
        for p in ps:
            print(f"    {p.name}: tools={len(p.tools)} kind={p.kind}")
            check(p.tools == boss.tools, f"{p.name} 工具集 = 大管家（T_ALL）")
            check(p.kind == "main", f"{p.name} kind=main")
            check(p.facts is boss.facts, f"{p.name} 事实数据 = 大管家")
        check(cast.get(customs[0]["id"], cfg).name == "白洲千夏", "按 id 取到白洲千夏")
        check(cast.get(customs[1]["id"], cfg).name == "小猫铃", "按 id 取到小猫铃")

        # 3) 全部角色 = 2 自建 + 7 内置
        print("[3] 角色总表")
        allp = cast.all_personas(cfg)
        print(f"    共 {len(allp)} 个：{[p.name for p in allp]}")
        check(len(allp) == 9, "2 自建 + 7 内置 = 9")
        check(allp[0].name == "白洲千夏", "自建排在前面")
        check(any(p.id == "miyagi" for p in allp), "内置大管家仍在")

        # 4) 删掉其中一个，另一个还在
        print("[4] 删一个不影响另一个")
        r = client.post("/settings", data={"section": "persona_del",
                                           "persona_id": customs[0]["id"]})
        check(r.status_code in (302, 303), "删除请求成功")
        left = read_cfg()["chat"].get("custom_personas") or []
        check(len(left) == 1, f"剩 1 个（实际 {len(left)}）")
        check(left[0]["name"] == "小猫铃", "剩下的是小猫铃")

        # 5) 推荐分配：大管家 deepseek、其余 zhipu
        print("[5] 推荐分配")
        read_cfg()
        raw2 = read_cfg()
        raw2["chat"]["models"] = []
        raw2["chat"]["persona_models"] = {}
        raw2["chat"]["custom_personas"] = []
        CFG.write_text(json.dumps(raw2, ensure_ascii=False, indent=2), encoding="utf-8")
        client.post("/settings", data={"section": "model_add", "provider": "deepseek",
                                       "label": "DeepSeek", "model": "deepseek-chat",
                                       "base_url": "https://api.deepseek.com/v1",
                                       "api_key": "sk-ds-1111"})
        client.post("/settings", data={"section": "model_add", "provider": "zhipu",
                                       "label": "智谱", "model": "glm-4-flash",
                                       "base_url": "https://open.bigmodel.cn/api/paas/v4",
                                       "api_key": "sk-zp-2222"})
        models = read_cfg()["chat"].get("models") or []
        ds = next(m for m in models if m["provider"] == "deepseek")
        zp = next(m for m in models if m["provider"] == "zhipu")
        pm = read_cfg()["chat"].get("persona_models") or {}
        print(f"    persona_models = {pm}")
        check(pm.get("miyagi") == ds["id"], "大管家 → DeepSeek")
        for pid in ("shiori", "miku", "rin", "yuki", "aju", "shirakaba"):
            check(pm.get(pid) == zp["id"], f"{pid} → 智谱")

        cfg2 = load_config()
        c_boss = load_chat_config(cfg2, "deepseek", "miyagi")
        c_other = load_chat_config(cfg2, "zhipu", "shiori")
        check(c_boss.model == "deepseek-chat", "大管家运行时用 deepseek-chat")
        check(c_other.model == "glm-4-flash", "其他角色运行时用 glm-4-flash")

        # 6) 页面能渲染自建角色
        print("[6] 页面渲染")
        page = client.get("/settings").get_data(as_text=True)
        check("自建角色" in page, "有自建角色区块")
        check("AI 写人设" in page, "有 AI 生成按钮")
        check("大管家能力" in page or "大管家的全套能力" in page, "说明继承大管家能力")

    finally:
        shutil.move(str(BACKUP), str(CFG))
        print("\nconfig.json 已还原")

    print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败: {fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
