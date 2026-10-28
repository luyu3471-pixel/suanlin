"""Browser regression checks for recovery, submission safety and task workflows."""
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
    directory = ROOT / "tmp" / ("workflow-qa-" + uuid.uuid4().hex[:8])
    out = ROOT / "tmp/workflow-review"
    out.mkdir(parents=True, exist_ok=True)
    server = create_server(port=0, directory=directory)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    workers, checks, errors = [], [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
            context = browser.new_context(viewport={"width":1440, "height":1000})
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("**/api/status", lambda route: route.abort())
            page.goto(url, wait_until="networkidle")
            expect(page.get_by_role("heading", name="暂时无法加载工作空间")).to_be_visible()
            expect(page.locator("#connection-notice")).to_be_visible()
            page.unroute("**/api/status")
            page.locator("#connection-notice button").click()
            expect(page.get_by_role("heading", name="协同工作台", exact=True)).to_be_visible()
            expect(page.locator("#connection-notice")).to_be_hidden()
            checks.append("initial connection failure has a useful recovery screen and retry succeeds")

            page.get_by_role("button", name="创建任务", exact=True).click()
            expect(page.locator("#task-node-hint")).to_contain_text("还没有在线节点")
            page.get_by_role("button", name="填入示例", exact=True).click()
            expect(page.locator("#event-count")).to_contain_text("3 / 128")
            expect(page.locator("#fill-example")).to_be_disabled()
            assert server.store.snapshot()["metrics"]["total"] == 0
            field = page.locator('[name="events"]')
            field.fill("\n".join(["办公室正常"] * 129))
            page.get_by_role("button", name="提交任务").click()
            assert not field.evaluate("e=>e.validity.valid")
            assert server.store.snapshot()["metrics"]["total"] == 0
            field.fill("检" * 2001)
            assert not field.evaluate("e=>e.validity.valid")
            field.fill("😀" * 2000)
            assert field.evaluate("e=>e.validity.valid")
            field.fill(" \n ")
            assert not field.evaluate("e=>e.validity.valid")
            checks.append("example never submits automatically; blank, line and Unicode length limits match server rules")

            field.fill("消防出口被纸箱堵塞\n配电箱发现浓烟和明火")
            page.locator('[name="name"]').fill("实时详情验证")
            page.evaluate("""() => {
                window.realFetch = window.fetch;
                window.fetch = async (...args) => {
                    const result = await realFetch(...args);
                    if(args[0] === '/api/tasks' && args[1]?.method === 'POST')
                        await new Promise(r=>setTimeout(r,2400));
                    return result;
                };
                document.querySelector('#task-form').requestSubmit();
                document.querySelector('#task-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
            }""")
            expect(page.locator('#task-form button[type="submit"]')).to_be_disabled()
            expect(page.locator("#create-dialog")).to_be_hidden(timeout=10000)
            assert server.store.snapshot()["metrics"]["total"] == 1
            page.evaluate("window.fetch=window.realFetch")
            checks.append("double submission produces one task and shows a pending state")

            page.locator('a[data-page="tasks"]').click()
            page.get_by_role("button", name="查看详情").first.click()
            expect(page.locator("#detail-content .status")).to_have_text("待调度")
            expect(page.locator(".task-guidance")).to_contain_text("还没有在线节点")
            expect(page.locator(".submitted-events")).to_contain_text("消防出口被纸箱堵塞")
            worker = subprocess.Popen([
                sys.executable, "-m", "suanlin.worker", "--coordinator", url,
                "--token-file", str(directory / "access_token"),
                "--name", "流程验证节点", "--id", "workflow-worker"
            ], cwd=ROOT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            workers.append(worker)
            expect(page.locator("#detail-content .status")).to_have_text("已完成", timeout=20000)
            expect(page.get_by_text("通道占用", exact=True)).to_be_visible()
            expect(page.get_by_text("烟雾风险", exact=True)).to_be_visible()
            assert page.locator(".task-timeline li.done").count() == 3
            page.screenshot(animations="disabled", path=str(out / "live-detail.png"))
            page.locator('#detail-dialog [data-action="close-detail"]').click()
            checks.append("queued detail explains missing nodes and updates to real results without reopening")

            # Keep new tasks queued to exercise counts, sorting and pending mutations.
            page.request.post(url + "/api/nodes/workflow-worker", data={"draining": True})
            task_a = page.request.post(url + "/api/tasks", data={"name":"筛选记录 A","events":["正常运行"]}).json()
            time.sleep(.02)
            task_b = page.request.post(url + "/api/tasks", data={"name":"筛选记录 B","events":["正常运行"]}).json()
            expect(page.locator('[data-filter="queued"] b')).to_have_text("2", timeout=8000)
            page.locator('[data-filter="queued"]').click()
            expect(page.locator("#task-filter")).to_have_value("queued")
            assert page.locator("tbody tr").count() == 2
            page.locator("#task-sort").select_option("oldest")
            expect(page.locator("tbody tr").first).to_have_attribute("data-key", "task-" + task_a["id"])
            page.locator("#task-sort").select_option("newest")
            expect(page.locator("tbody tr").first).to_have_attribute("data-key", "task-" + task_b["id"])
            page.locator("#task-search").fill("不会存在的记录")
            expect(page.get_by_role("heading", name="没有符合条件的任务")).to_be_visible()
            assert page.get_by_text("从第一个任务开始", exact=True).count() == 0
            page.locator(".task-empty [data-action='reset-filters']").click()
            expect(page.locator("#task-filter")).to_have_value("all")
            expect(page.locator("#task-search")).to_have_value("")
            expect(page.locator("tbody tr")).to_have_count(3)
            page.locator("#task-sort").select_option("latency")
            expect(page.locator("tbody tr").first.locator(".status")).to_have_text("已完成")
            checks.append("status counts, filter synchronization, ordering and distinct no-match state work")

            page.evaluate("""() => {
                window.demoCalls=0;
                window.fetch=async (...args)=>{
                    if(args[0]==='/api/demo') {
                        window.demoCalls++;
                        await new Promise(r=>setTimeout(r,3000));
                    }
                    return realFetch(...args);
                };
            }""")
            page.locator('[data-action="demo"]').click()
            page.wait_for_timeout(1800)
            expect(page.locator('[data-action="demo"]')).to_be_disabled()
            page.locator('[data-action="demo"]').evaluate("e=>e.dispatchEvent(new MouseEvent('click',{bubbles:true}))")
            page.wait_for_timeout(2000)
            expect(page.locator('[data-action="demo"]')).to_be_enabled(timeout=10000)
            assert page.evaluate("demoCalls") == 1
            assert server.store.snapshot()["metrics"]["total"] == 15
            page.evaluate("window.fetch=window.realFetch")
            checks.append("live polling cannot re-enable an in-flight action or duplicate demo tasks")

            page.route("**/api/status", lambda route: route.abort())
            expect(page.locator("#connection-notice")).to_be_visible(timeout=8000)
            expect(page.locator("#connection-message")).to_contain_text("当前显示")
            expect(page.locator("tbody tr")).to_have_count(15)
            page.unroute("**/api/status")
            page.locator("#connection-notice button").click()
            expect(page.locator("#connection-notice")).to_be_hidden()
            checks.append("disconnect retains records with stale-data notice and recovers cleanly")

            page.route("**/api/experiments", lambda route: route.abort())
            page.locator('a[data-page="experiments"]').click()
            expect(page.get_by_role("button", name="重新加载", exact=True)).to_be_visible()
            expect(page.locator("#connection")).to_contain_text("协调服务在线")
            page.unroute("**/api/experiments")
            page.get_by_role("button", name="重新加载", exact=True).click()
            expect(page.locator(".chart-panel")).to_be_visible()
            checks.append("optional experiment failure stays separate from coordinator availability")

            # Inject a simulated snapshot to verify the four-node diagram does not undercount.
            page.locator('a[data-page="overview"]').click()
            page.evaluate("""() => {
                state.nodes=Array.from({length:6},(_,i)=>({...state.nodes[0],id:'preview-'+i,name:'预览节点 '+i,online:true}));
                render();
            }""")
            expect(page.get_by_text("6 个节点在线", exact=True)).to_be_visible()
            expect(page.locator(".topology-more")).to_contain_text("查看全部 6 个")
            assert page.locator(".network-node:not(.center)").count() == 4
            page.evaluate("refresh(true)")
            checks.append("diagram displays a truthful total when more than four nodes are online")

            page.locator('a[data-page="tasks"]').click()
            for width in [1440,768,390,360]:
                page.set_viewport_size({"width":width,"height":900 if width>700 else 844})
                page.wait_for_timeout(700)
                assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
                page.screenshot(animations="disabled", path=str(out / f"tasks-{width}.png"), full_page=True)
                page.get_by_role("button", name="创建任务", exact=True).click()
                expect(page.locator("#task-node-hint")).to_contain_text("暂停")
                assert page.locator("#create-dialog").evaluate("e=>e.scrollWidth<=e.clientWidth")
                page.get_by_role("button", name="填入示例").click()
                page.get_by_role("button", name="提交任务").scroll_into_view_if_needed()
                if width == 390:
                    page.screenshot(animations="disabled", path=str(out / "form-mobile.png"))
                page.locator('#create-dialog [data-action="close-dialog"]').first.click()
                page.locator('[name="events"]').evaluate("e=>{e.value='';e.dispatchEvent(new Event('input',{bubbles:true}));}")
            checks.append("filters, examples and longer dialogs fit desktop, tablet and small phones")

            # Error responses keep form text so users can correct or retry.
            page.get_by_role("button", name="创建任务", exact=True).click()
            field.fill("需要保留的输入")
            page.route("**/api/tasks", lambda route: route.fulfill(status=503, content_type="application/json", body='{"error":"服务暂不可用"}'))
            page.get_by_role("button", name="提交任务").click()
            expect(page.locator("#form-error")).to_contain_text("服务暂不可用")
            expect(field).to_have_value("需要保留的输入")
            expect(page.locator('#task-form button[type="submit"]')).to_be_enabled()
            checks.append("failed submission keeps input and presents an inline recovery message")

            login_context = browser.new_context()
            login_page = login_context.new_page()
            login_page.on("pageerror", lambda e: errors.append(str(e)))
            login_page.goto(url, wait_until="networkidle")
            expect(login_page.get_by_role("heading", name="协同工作台", exact=True)).to_be_visible()
            login_context.clear_cookies()
            expect(login_page.locator("#login-dialog")).to_be_visible(timeout=8000)
            login_page.locator('[name="token"]').fill("incorrect-test-token")
            login_page.get_by_role("button", name="连接工作空间", exact=True).click()
            expect(login_page.locator("#toast")).to_contain_text("接入令牌不正确")
            expect(login_page.locator('#login-form button[type="submit"]')).to_be_enabled()
            login_page.locator('[name="token"]').fill(server.store.token)
            login_page.get_by_role("button", name="连接工作空间", exact=True).click()
            expect(login_page.locator("#login-dialog")).to_be_hidden()
            expect(login_page.locator("#connection-notice")).to_be_hidden(timeout=8000)
            login_context.close()
            checks.append("expired session can log in again and invalid tokens get a specific error")

            assert not errors, errors
            browser.close()
        files = ["web/app.js", "web/style.css", "web/index.html"]
        result = {"checks":checks, "javascript_errors":errors, "passed":True,
                  "isolated_test_data":True, "existing_user_state_modified":False,
                  "source_sha256":{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}}
        (ROOT/"results/workflow_review.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(f"Passed {len(checks)} workflow checks; no browser errors.")
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
