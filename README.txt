算邻 Qwen 双机调度软件包 2.5.0（实测修正版）
发布日期：2026-11-12

这是 Windows 解压运行包，包含 Python 源码与启动器，不是独立 EXE。
需要 Python 3.10+；核心系统仅依赖标准库，无需 pip install。
不包含 Qwen 模型文件、llama-server 运行时、接入令牌或数据库。

A 端使用
1. 解压到可写目录；不要在 ZIP 内直接运行。已有服务占用8765时不要重复启动。
2. 先启动已有的 Qwen 模型服务，默认 http://127.0.0.1:8086/v1。
   若需要自行启动，下面是已实测参数模板（在对应 llama-server 目录执行，替换完整模型路径）：
   llama-server.exe -m "模型完整路径\Qwen3.5-9B-Q6_K.gguf" --host 127.0.0.1 --port 8086 --alias qwen3.5-9b --gpu-layers all --ctx-size 16384 --parallel 2 --reasoning off --jinja
3. 双击 Start-A.cmd。默认启动协调器和本机四槽节点；浏览器打开本机页面。
4. 首次运行生成 .local\runtime\access_token。令牌仅通过可信方式提供给 B。

B 端使用
1. 在 B 解压同一软件包；更新前先停止 B 原执行程序，避免节点 ID 冲突。
2. 编辑 settings.json 的 coordinator_url，填 A 的局域网地址；当前实测为 http://192.168.5.43:8765。
3. 将 A 的 access_token 保存为 B 包根目录的 token.txt。不要填写引号、不要上传到公开位置。
4. 双击 Start-B.cmd。B 不需要模型或 GPU；默认节点 ID office-b、四槽并发。
5. 在 A 的节点页核实 B 的主机及来源地址，然后设为可信。

运行配置
settings.json 可修改端口、并发数、模型地址和模型名称。
A 端改 port 后，同时修改 B 的 coordinator_url；仅访问受信任的局域网。
如 Python 未被自动找到，可设置环境变量 SUANLIN_PYTHON 为 python.exe 的完整路径。
关闭：在启动窗口按 Ctrl+C。请等待子进程退出后再关闭窗口。
直接命令：python package_launcher.py A 或 python package_launcher.py B。
只检查配置：python package_launcher.py A --check。

包内内容
suanlin/、web/：核心服务和网页。
tests/：本地整合后70项回归测试；命令 python -m unittest discover -s tests -v。
reports/single-dual-comparison/：最新三轮单机/双机对照，324项任务。
reports/dual-validation/：之前双机正常与节点暂停场景的原始证据。
MANIFEST-SHA256.json：打包文件逐项 SHA256。

最新实测结论
12项混合短任务三轮平均：A单机6.60秒，B单机12.70秒，双机12.85秒。
24项重任务三轮平均：A单机86.05秒，B单机91.87秒，双机59.08秒。
324项任务全部执行完成且按时；双机108项中72项Qwen、36项回退。
模型响应校验异常和等待预算回退仍存在；压缩跨机摘要差异未解决。
B实测沿用原执行端，包内新版B进度上报和取消尚未在真实B机器验收。
本包不宣称上述问题已经解决；不包含第10项新指标体系。
QWEN_UPGRADE.txt保留实现过程及历史验证记录，以本说明及最新 reports/single-dual-comparison 为当前性能结论。

本地整合说明（2026-11-12）
保留本地新手引导、简化表单、结果展示及AI/6G说明，加入2.5.0进度和滚动排程显示。
修复同一时钟刻度或时钟回拨时，新到紧急任务未使旧排程失效的问题。
delivery中的PDF与视频保留2.4.1实测依据；代码与材料版本在交付清单中分别记录。
本次只做本机回归和浏览器验证，未新增双机、Qwen模型或6G实测。详见docs/本地整合说明.md。
