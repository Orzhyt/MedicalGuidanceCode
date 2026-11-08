# 本机 Dify 的运行注意事项

核验环境：2026-10-11，Windows，D:/codex/dify，Dify 1.9.1。先核对当前状态，再使用这些已知路径；其他主机不默认相同。

## 既有部署

- 本机已有应用在 Ubuntu-24.04 的独立 Docker Engine，使用 wsl.exe -d Ubuntu-24.04 -u root 管理；不是 Docker Desktop 的引擎。不要为找不到容器而初始化新安装。
- 优先读取 D:/codex/dify/Dify启动指引.md。已有启动脚本为 scripts/Start-Dify-Ubuntu.ps1；平时启动现有容器，不拉镜像或重建。
- 本机已验证需要保留 Ubuntu 交互会话以维持运行。不要关闭任务已有的保持运行会话。WSL 配置不能替代实际容器稳定检查。
- 页面用 http://127.0.0.1/apps。已有指南说明 localhost 和 Desktop socket 历史故障；不能据此关闭全局防火墙或改代理。
- 不执行 down -v、prune、wsl --unregister、删除虚拟磁盘等操作来排查启动。

## 医院导诊目录与服务

- 文件位于 D:/codex/dify/hospital_guidance。使用 installed.json、实际 DSL 和控制台核对当前应用，而不是按历史编号猜测。
- 医院接口本机端口8095；容器通过 http://medical-guidance:8095 访问，挂在 docker_default 网络。Dify 的 SSRF 代理须允许这个服务名，保留原有其他业务配置，不替换整个代理白名单。
- 模型沿用已配置并实测可用的模型。当前 DeepSeek provider 的配置名为 deepseek-flash；不因显示名与产品名称不一致擅自重新配置。
- private/service_credentials.json 和 private/dify_api_keys.json 是本机私密配置，只内部读取；不要打印、打包或写入 skill。
- 医院 SQLite 使用持久化卷。测试创建独立患者和请求，结束只释放自己创建的记录，不重置用户数据库。

## 在 API 容器执行

- 容器工作目录 /tmp/hospital_guidance 可运行独立节点构建和单元测试；项目文件从 Windows 路径针对性复制。
- 涉及 Flask、AppDslService 导入、发布或 Dify 密钥存储的内部脚本，保留 API 默认工作目录 /app/api，并设置 PYTHONPATH=/app/api。改到 /tmp 可能让相对密钥存储路径失效。
- 内部执行器是 /app/api/.venv/bin/python。容器内部 API 地址为 http://nginx/v1。
- 优先原位更新指定应用，保留其他应用、原凭据、知识库和发布设置。只修改咨询流程时，不重发无关预约应用。
- 全目录同步会覆盖容器刚生成的 reports。先同步源码再测试；测试后只回拷 exports、reports 和必要本机私有配置，不用整个目录覆盖新文档。
- 核对实际发布的 DSL，不把草稿构建文件当成已发布版本。导出用 include_secret=False，额外扫描交付文件及 DOCX XML 是否包含实际密钥或患者签名令牌。

本项目只调整独立服务和 DSL 时，运行适合改动的检查；不把 Dify 核心全库 QA 当成例行反馈迭代。确实修改核心 api/web 时，按项目 AGENTS.md 执行对应 QA。
