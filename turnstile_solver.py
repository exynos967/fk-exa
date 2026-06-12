"""
外置 Turnstile Solver 调用工具。
对接 https://github.com/Theyka/Turnstile-Solver 的 API。
"""
import time

import requests as std_requests

from config import TURNSTILE_SOLVER_URL

_SOLVER_BASE = TURNSTILE_SOLVER_URL.rstrip("/")


def solve_turnstile(url, sitekey, action=None, cdata=None, timeout=60):
    """调用外置 Turnstile Solver 获取 token。

    Args:
        url: 包含 Turnstile 的页面 URL
        sitekey: Turnstile sitekey
        action: 可选 action 参数
        cdata: 可选 cdata 参数
        timeout: 最长等待时间（秒）

    Returns:
        token 字符串，失败返回 None
    """
    params = {"url": url, "sitekey": sitekey}
    if action:
        params["action"] = action
    if cdata:
        params["cdata"] = cdata

    # 1. 提交任务
    try:
        r = std_requests.get(
            f"{_SOLVER_BASE}/turnstile",
            params=params,
            timeout=10,
        )
        if r.status_code != 200:
            print(f"❌ Solver 请求失败: HTTP {r.status_code}")
            return None
        data = r.json()
    except Exception as e:
        print(f"❌ Solver 请求异常: {e}")
        return None

    # Turnstile-Solver 可能返回 task_id 或 taskId
    task_id = data.get("task_id") or data.get("taskId")
    if not task_id:
        # 有些版本把 errorId:0 当作成功信号
        if data.get("errorId") == 0 and data.get("taskId"):
            task_id = data["taskId"]
        else:
            print(f"❌ Solver 未返回 task_id: {data}")
            return None

    # 2. 轮询结果
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = std_requests.get(
                f"{_SOLVER_BASE}/result",
                params={"id": task_id},
                timeout=10,
            )
            if r.status_code != 200:
                time.sleep(1)
                continue
            result = r.json()
        except Exception:
            time.sleep(1)
            continue

        # 已解决 — 格式1: {value: "token", elapsed_time: N}
        if "value" in result and result["value"] and result["value"] != "CAPTCHA_FAIL":
            elapsed = result.get("elapsed_time", 0)
            token = result["value"]
            print(f"✅ Turnstile 已解决 (耗时 {elapsed}s)")
            return token

        # 已解决 — 格式2: {errorId: 0, status: "ready", solution: {token: "..."}}
        if result.get("status") == "ready" and "solution" in result:
            token = result["solution"].get("token", "")
            if token:
                print(f"✅ Turnstile 已解决")
                return token

        # 处理中
        if result.get("status") == "processing":
            time.sleep(1)
            continue

        # 已失败 (errorId != 0 且不是 ready/processing)
        if "errorId" in result and result["errorId"] != 0:
            print(f"❌ Solver 返回错误: {result.get('errorDescription', result)}")
            return None

        time.sleep(1)

    print("❌ Turnstile 解决超时")
    return None
