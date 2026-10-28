"""Exercise motion, live DOM updates and input stability with isolated local workers."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".tools"))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".tools/browsers"))
from playwright.sync_api import sync_playwright, expect
from suanlin.server import create_server


def main():
    directory = ROOT / "tmp" / ("motion-qa-" + uuid.uuid4().hex[:8])
    server = create_server(port=0, directory=directory)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    workers, checks, errors = [], [], []
    try:
        for i in range(2):
            workers.append(subprocess.Popen([
                sys.executable, "-m", "suanlin.worker", "--coordinator", url,
                "--token-file", str(directory / "access_token"),
                "--name", f"本机执行节点 {i + 1}", "--id", f"local-{i + 1}"
            ], cwd=ROOT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
        deadline = time.monotonic() + 12
        while len([n for n in server.store.snapshot()["nodes"] if n["online"]]) != 2:
            if time.monotonic() > deadline:
                raise RuntimeError("Isolated workers did not connect")
            time.sleep(.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
            context = browser.new_context(viewport={"width":1440, "height":1000}, reduced_motion="no-preference")
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url, wait_until="networkidle")
            expect(page.locator("html")).to_have_attribute("data-motion", "on")
            page.wait_for_timeout(1100)
            assert page.evaluate("""() => {
                window.savedHero = document.querySelector('.hero');
                window.savedFlow = document.querySelector('.hero-flow').getAnimations()[0];
                window.savedFlowTime = window.savedFlow.currentTime;
                return document.getAnimations().filter(a=>a.playState==='running').length >= 8;
            }""")
            checks.append("multiple continuous effects are playing")
            page.wait_for_timeout(3200)
            assert page.evaluate("""() =>
                savedHero === document.querySelector('.hero') &&
                savedFlow === document.querySelector('.hero-flow').getAnimations()[0] &&
                savedFlow.currentTime > savedFlowTime + 2500
            """)
            checks.append("polling preserves elements and continuous animation timelines")

            page.locator(".metric").first.hover()
            page.wait_for_timeout(350)
            assert page.locator(".metric").first.evaluate("""e =>
                getComputedStyle(e).transform !== 'none' && e.style.getPropertyValue('--fx-x') !== ''
            """)
            page.wait_for_timeout(1700)
            assert page.locator(".metric").first.evaluate("e => e.style.getPropertyValue('--fx-x') !== ''")
            checks.append("hover lift and pointer light survive a live refresh")

            page.locator(".hero-action").click()
            expect(page.locator(".metric-number").nth(1)).to_have_attribute("data-value", "12", timeout=20000)
            page.wait_for_timeout(800)
            assert page.locator(".metric-number").nth(1).inner_text() == "12"
            status = page.request.get(url + "/api/status").json()
            assert status["metrics"]["completed"] == 12
            for element in page.locator(".metric-number").all():
                assert element.evaluate("""e => {
                    const n=e.dataset.value===''?NaN:Number(e.dataset.value);
                    const expected=!Number.isFinite(n)?'—':e.dataset.format==='percent'?n.toFixed(1)+'%':
                        n.toLocaleString('zh-CN',{maximumFractionDigits:e.dataset.format==='integer'?0:1});
                    return e.textContent===expected;
                }""")
            checks.append("counters settle to exact API values after real task execution")

            page.get_by_role("button", name="创建任务", exact=True).click()
            assert page.locator(".fx-ripple").count() > 0
            expect(page.locator("#create-dialog")).to_be_visible()
            assert page.locator("#create-dialog").evaluate("e => getComputedStyle(e).animationName.includes('dialogArrive')")
            page.locator('[name="name"]').fill("输入保持验证")
            page.locator('[name="events"]').fill("办公室设备运行正常")
            page.wait_for_timeout(1800)
            assert page.locator('[name="name"]').input_value() == "输入保持验证"
            assert page.locator('[name="events"]').input_value() == "办公室设备运行正常"
            assert page.locator(".fx-ripple").count() == 0
            page.get_by_role("button", name="取消", exact=True).click()
            checks.append("button ripples clean up and dialog fields remain intact during polling")

            page.locator('a[data-page="tasks"]').click()
            expect(page.get_by_role("heading", name="任务中心", exact=True)).to_be_visible()
            page.wait_for_timeout(850)
            search = page.locator("#task-search")
            search.fill("合成巡检")
            search.evaluate("e => { window.savedSearch=e; e.setSelectionRange(2,4); }")
            page.wait_for_timeout(3200)
            assert page.evaluate("""() => savedSearch===document.querySelector('#task-search') &&
                document.activeElement===savedSearch && savedSearch.value==='合成巡检' &&
                savedSearch.selectionStart===2 && savedSearch.selectionEnd===4""")
            checks.append("search focus and selected text survive multiple live refreshes")
            search.fill("")
            page.locator("#task-filter").select_option("completed")
            page.locator("#task-filter").focus()
            page.locator("#task-filter").evaluate("e => { window.savedFilter=e; }")
            page.wait_for_timeout(1700)
            assert page.evaluate("""() => savedFilter===document.querySelector('#task-filter') &&
                savedFilter.value==='completed' && document.activeElement===savedFilter""")
            checks.append("task filter retains its selection and focus")

            page.evaluate("() => { window.savedRow=document.querySelector('tbody tr'); }")
            response = page.request.post(url + "/api/tasks", data={
                "name":"动效验证任务", "events":["办公室设备运行正常"], "policy":"smart",
                "privacy":"local_only", "deadline_ms":5000, "compute_loops":0
            })
            assert response.status == 201
            expect(page.locator("tbody tr").filter(has_text="动效验证任务")).to_be_visible(timeout=10000)
            assert page.evaluate("""() => savedRow.isConnected &&
                savedRow===document.querySelector('[data-key="'+savedRow.dataset.key+'"]')""")
            checks.append("new task insertion preserves existing keyed rows")
            with page.expect_download() as download:
                page.get_by_role("button", name="导出记录", exact=False).click()
            assert download.value.suggested_filename == "任务调度记录.csv"
            checks.append("CSV export remains available with motion enabled")

            page.set_viewport_size({"width":390, "height":844})
            page.locator(".table-wrapper").evaluate("e => { window.savedTable=e; e.scrollLeft=90; }")
            page.wait_for_timeout(1700)
            assert page.evaluate("""() => savedTable===document.querySelector('.table-wrapper') &&
                savedTable.scrollLeft===90""")
            checks.append("horizontal table scroll survives polling")
            assert page.locator("td").first.evaluate("e => parseFloat(getComputedStyle(e).fontSize)") >= 13
            assert page.locator(".page-heading p").evaluate("e => parseFloat(getComputedStyle(e).fontSize)") >= 13
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
            checks.append("mobile task text is at least 13px and body stays within the viewport")

            page.set_viewport_size({"width":1440, "height":1000})
            page.locator('a[data-page="experiments"]').click()
            page.wait_for_timeout(1200)
            page.locator(".bar-fill").first.evaluate("e => { window.savedBar=e; }")
            page.get_by_role("button", name="受限带宽", exact=True).click()
            page.wait_for_timeout(150)
            assert page.evaluate("""() => savedBar===document.querySelector('.bar-fill') &&
                savedBar.getAnimations().some(a=>a.transitionProperty==='width')""")
            page.wait_for_timeout(1000)
            assert page.locator(".bar-fill").first.evaluate("""e => Math.abs(
                e.getBoundingClientRect().width -
                e.parentElement.getBoundingClientRect().width * parseFloat(e.style.getPropertyValue('--bar')) / 100
            ) < 1.5""")
            checks.append("scenario changes animate the real chart width to its correct result")

            page.locator("#motion-toggle").click()
            expect(page.locator("html")).to_have_attribute("data-motion", "off")
            page.wait_for_timeout(80)
            assert page.evaluate("document.getAnimations().filter(a=>a.playState==='running').length") == 0
            page.reload(wait_until="networkidle")
            expect(page.locator("html")).to_have_attribute("data-motion", "off")
            assert page.evaluate("document.getAnimations().filter(a=>a.playState==='running').length") == 0
            checks.append("motion switch stops effects and persists after reload")
            page.locator("#motion-toggle").click()
            expect(page.locator("html")).to_have_attribute("data-motion", "on")
            checks.append("motion can be enabled again")

            reduced_context = browser.new_context(viewport={"width":390,"height":844}, reduced_motion="reduce")
            quiet = reduced_context.new_page()
            quiet.on("pageerror", lambda e: errors.append(str(e)))
            quiet.goto(url, wait_until="networkidle")
            expect(quiet.locator("html")).to_have_attribute("data-motion", "off")
            expect(quiet.locator("#motion-toggle")).to_be_disabled()
            quiet.wait_for_timeout(800)
            assert quiet.evaluate("document.getAnimations().filter(a=>a.playState==='running').length") == 0
            assert quiet.locator(".metric-number").nth(1).inner_text() == "13"
            checks.append("system reduced-motion preference disables effects and presents exact numbers")
            assert not errors, errors
            reduced_context.close()
            context.close()
            browser.close()
        result = {
            "date":"2026-10-28", "checks":checks, "javascript_errors":errors, "passed":True,
            "isolated_test_data":True, "existing_user_state_modified":False,
            "source_sha256":{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ("web/app.js","web/style.css","web/index.html")}
        }
        (ROOT/"results/motion_review.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps({"motion_checks":len(checks),"passed":True,"browser_errors":errors}))
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
        for worker in workers:
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(3)
        server.store.db.close()


if __name__ == "__main__":
    main()
